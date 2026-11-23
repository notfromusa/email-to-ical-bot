"""Web UI authentication, session cookies, CSRF validation, rate limiting, and security headers."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urlparse

from flask import (
    Flask,
    Response,
    g,
    has_request_context,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask.sessions import SecureCookieSessionInterface
from werkzeug.security import check_password_hash, generate_password_hash

import config
from ical_bot.web.state import AppContext, get_web_ctx

_DUMMY_PASSWORD_HASH = generate_password_hash("dummy-timing-equalizer-secret")


class DynamicSecureCookieSessionInterface(SecureCookieSessionInterface):
    """Session interface setting ``Secure`` dynamically when served over HTTPS."""

    def get_cookie_secure(self, app_obj: Flask) -> bool:
        """Return True if ``SESSION_COOKIE_SECURE`` is enabled or the current request uses HTTPS."""
        if app_obj.config.get("SESSION_COOKIE_SECURE"):
            return True
        if has_request_context():
            return bool(request.is_secure)
        return False


class LoginRateLimiter:
    """Thread-safe sliding-window per-IP rate limiter for authentication attempts."""

    def __init__(self, *, max_attempts: int = 5, window_seconds: int = 900) -> None:
        """Initialize the sliding-window rate limiter.

        Args:
            max_attempts: Maximum failed attempts permitted within ``window_seconds``.
            window_seconds: Lookback window duration in seconds.
        """
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Clear all recorded failed attempts."""
        with self._lock:
            self._attempts.clear()

    def is_limited(self, client_key: str) -> Tuple[bool, int]:
        """Check whether ``client_key`` is temporarily rate-limited."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            attempts = [ts for ts in self._attempts.get(client_key, []) if ts > cutoff]
            self._attempts[client_key] = attempts
            if len(attempts) >= self.max_attempts:
                retry_after = max(1, int(self.window_seconds - (now - attempts[0])))
                return True, retry_after
        return False, 0

    def record_failure(self, client_key: str) -> None:
        """Record a failed login timestamp for ``client_key``."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            attempts = [ts for ts in self._attempts.get(client_key, []) if ts > cutoff]
            attempts.append(now)
            self._attempts[client_key] = attempts

    def clear(self, client_key: str) -> None:
        """Clear failure history after a successful authentication."""
        with self._lock:
            self._attempts.pop(client_key, None)


def auth_store_path() -> Path:
    """Return the configured filesystem path for UI operator credentials."""
    return Path(getattr(config, "UI_AUTH_STORE_PATH", "data/ui_auth.json"))


def auth_secret_path() -> Path:
    """Return the configured filesystem path for the persistent Flask session signing key."""
    return Path(getattr(config, "UI_AUTH_SECRET_PATH", "data/ui_auth_secret.key"))


def auth_configured() -> bool:
    """Return True if an operator password hash exists in the auth store."""
    store = auth_store_path()
    if not store.exists():
        return False
    try:
        payload = json.loads(store.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(payload.get("password_hash"))


def auth_requires_change() -> bool:
    """Return True when stored credentials require forced password rotation."""
    payload = load_auth()
    return bool(payload.get("password_hash")) and bool(payload.get("must_change"))


def load_auth() -> Dict:
    """Load the JSON operator credential store, or empty dict if absent/invalid."""
    store = auth_store_path()
    if not store.exists():
        return {}
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_auth(username: str, password_hash: str) -> None:
    """Persist UI authentication credentials atomically with 0600 permissions."""
    store = auth_store_path()
    payload = {"username": username, "password_hash": password_hash, "must_change": False}
    config.write_private_file(store, json.dumps(payload))


def verify_credentials(username: str, password: str) -> bool:
    """Verify username and password in constant time against stored hashes."""
    auth = load_auth()
    stored_username = str(auth.get("username") or "")
    stored_hash = str(auth.get("password_hash") or "")
    username_ok = bool(stored_username) and hmac.compare_digest(username, stored_username)
    candidate_hash = stored_hash if (username_ok and stored_hash) else _DUMMY_PASSWORD_HASH
    password_ok = check_password_hash(candidate_hash, password)
    return username_ok and password_ok


def check_auth_session() -> bool:
    """Return True when the current Flask session is marked authenticated."""
    return bool(session.get("authenticated"))


def credentials_ready() -> bool:
    """Return True when runtime mailbox credentials are available."""
    try:
        return config.has_email_password()
    except AttributeError:
        return bool(getattr(config, "EMAIL_PASSWORD", ""))


def check_auth_via_hooks(ctx: AppContext) -> bool:
    """Evaluate authentication status, honoring legacy ``web_ui._check_auth`` hooks if attached."""
    if ctx.hooks is not None and hasattr(ctx.hooks, "_check_auth"):
        return bool(ctx.hooks._check_auth())
    return check_auth_session()


def check_auth_configured_via_hooks(ctx: AppContext) -> bool:
    """Evaluate whether auth store is configured, honoring ``web_ui._auth_configured`` hooks."""
    if ctx.hooks is not None and hasattr(ctx.hooks, "_auth_configured"):
        return bool(ctx.hooks._auth_configured())
    return auth_configured()


def check_auth_requires_change_via_hooks(ctx: AppContext) -> bool:
    """Evaluate password rotation requirement, honoring ``web_ui._auth_requires_change`` hooks."""
    if ctx.hooks is not None and hasattr(ctx.hooks, "_auth_requires_change"):
        return bool(ctx.hooks._auth_requires_change())
    return auth_requires_change()


def check_credentials_ready_via_hooks(ctx: AppContext) -> bool:
    """Evaluate mailbox credential readiness, honoring ``web_ui._credentials_ready`` hooks."""
    if ctx.hooks is not None and hasattr(ctx.hooks, "_credentials_ready"):
        return bool(ctx.hooks._credentials_ready())
    return credentials_ready()


def is_local_request() -> bool:
    """Return True only for direct loopback requests without proxy forwarding headers."""
    remote_addr = request.remote_addr or ""
    if remote_addr not in {"127.0.0.1", "::1"}:
        return False
    if request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-IP") or request.headers.get("Forwarded"):
        return False
    return True


def is_secure_request() -> bool:
    """Return True only when Flask/Werkzeug (optionally via configured ProxyFix) reports HTTPS."""
    return bool(request.is_secure)


def normalize_host(netloc: str, scheme: str) -> str:
    """Normalize a ``host[:port]`` string by lowercasing and stripping default HTTP/HTTPS ports."""
    host = (netloc or "").strip().lower()
    if scheme == "http" and host.endswith(":80"):
        host = host[:-3]
    elif scheme == "https" and host.endswith(":443"):
        host = host[:-4]
    return host


def get_csrf_token() -> str:
    """Return or initialize the per-session CSRF synchronizer token."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return str(token)


def validate_csrf(app_logger) -> bool:
    """Validate same-origin (when non-null ``Origin`` is sent) and per-session CSRF token."""
    origin = request.headers.get("Origin")
    if origin and origin.strip().lower() != "null":
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or normalize_host(parsed.netloc, parsed.scheme) != normalize_host(
            request.host, request.scheme
        ):
            app_logger.warning(
                "CSRF validation failed: origin_mismatch (path=%s, origin=%r, host=%r)",
                request.path,
                origin,
                request.host,
            )
            return False
    submitted = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    expected = session.get("csrf_token")
    if not expected:
        app_logger.warning("CSRF validation failed: missing_session_token (path=%s)", request.path)
        return False
    if not submitted:
        app_logger.warning("CSRF validation failed: missing_submitted_token (path=%s)", request.path)
        return False
    if not hmac.compare_digest(str(submitted), str(expected)):
        app_logger.warning("CSRF validation failed: token_mismatch (path=%s)", request.path)
        return False
    return True


def ensure_secret_key(app_obj: Flask) -> None:
    """Load or atomically generate the persistent Flask session signing secret."""
    secret_path = auth_secret_path()
    if secret_path.exists():
        app_obj.secret_key = secret_path.read_text(encoding="utf-8").strip()
        try:
            os.chmod(secret_path, 0o600)
        except OSError:
            pass
        return
    secret = os.urandom(32).hex()
    config.write_private_file(secret_path, secret)
    app_obj.secret_key = secret


def register_security_hooks(app_obj: Flask) -> None:
    """Register context processor, auth/CSRF before_request, and strict CSP after_request hooks."""

    @app_obj.context_processor
    def inject_security_context():
        return {
            "csrf_token": get_csrf_token(),
            "csp_nonce": getattr(g, "csp_nonce", ""),
        }

    @app_obj.before_request
    def enforce_auth():
        ctx = get_web_ctx(app_obj)
        g.csp_nonce = secrets.token_urlsafe(16)
        path = request.path or ""
        if getattr(config, "REQUIRE_HTTPS_FOR_REMOTE", True) and not is_local_request() and not is_secure_request():
            return Response("HTTPS required", 400)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and app_obj.config.get("CSRF_ENABLED", True):
            if not validate_csrf(app_obj.logger):
                if path in {"/login", "/login/"}:
                    get_csrf_token()
                    return (
                        render_template(
                            "login.html",
                            error="CSRF token missing or invalid. The form has been refreshed — please try again.",
                        ),
                        403,
                    )
                if path in {"/setup", "/setup/"} and is_local_request():
                    get_csrf_token()
                    return (
                        render_template(
                            "setup.html",
                            error="CSRF token missing or invalid. The form has been refreshed — please try again.",
                        ),
                        403,
                    )
                if path.startswith("/api/"):
                    return jsonify({"error": "CSRF token missing or invalid"}), 403
                return Response("CSRF token missing or invalid", 403)
        if path.startswith("/setup") or path.startswith("/login") or path.startswith("/static") or path == "/favicon.ico":
            return None
        if not check_auth_configured_via_hooks(ctx):
            if not is_local_request():
                return Response("Authentication required", 401)
            if not ctx.auth_warning_logged:
                app_obj.logger.warning("Web UI authentication is not configured; setup required.")
                ctx.auth_warning_logged = True
            return redirect(url_for("auth.setup"))
        if check_auth_requires_change_via_hooks(ctx):
            return redirect(url_for("auth.setup"))
        if check_auth_via_hooks(ctx):
            get_csrf_token()
            return None
        if path.startswith("/api/"):
            return jsonify({"error": "Authentication required"}), 401
        return redirect(url_for("auth.login"))

    @app_obj.after_request
    def add_security_headers(response: Response) -> Response:
        nonce = getattr(g, "csp_nonce", "")
        csp = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "object-src 'none'"
        )
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if is_secure_request():
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        path = request.path or ""
        if path.startswith("/api/") or path in {"/", "/login", "/setup"}:
            response.headers.setdefault("Cache-Control", "no-store")
        return response


__all__ = [
    "DynamicSecureCookieSessionInterface",
    "LoginRateLimiter",
    "auth_configured",
    "auth_requires_change",
    "auth_secret_path",
    "auth_store_path",
    "check_auth_configured_via_hooks",
    "check_auth_requires_change_via_hooks",
    "check_auth_session",
    "check_auth_via_hooks",
    "check_credentials_ready_via_hooks",
    "credentials_ready",
    "ensure_secret_key",
    "get_csrf_token",
    "is_local_request",
    "is_secure_request",
    "load_auth",
    "normalize_host",
    "register_security_hooks",
    "save_auth",
    "validate_csrf",
    "verify_credentials",
]
