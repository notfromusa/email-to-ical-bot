"""Authentication, CSRF token, and runtime credential Blueprint routes."""

from __future__ import annotations

import secrets

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import generate_password_hash

import config
from ical_bot.web.auth import (
    check_auth_configured_via_hooks,
    check_auth_requires_change_via_hooks,
    check_credentials_ready_via_hooks,
    get_csrf_token,
    is_local_request,
    is_secure_request,
    save_auth,
    verify_credentials,
)
from ical_bot.web.state import get_web_ctx, resolve_runtime

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    """Initial local-only Web UI account setup with minimum password length enforcement."""
    ctx = get_web_ctx()
    if not is_local_request():
        return Response("Forbidden", 403)
    if check_auth_configured_via_hooks(ctx) and not check_auth_requires_change_via_hooks(ctx):
        return redirect(url_for("events.index"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        if not username or not password:
            error = "Username and password are required."
        elif len(password) < 12:
            error = "Password must be at least 12 characters long."
        else:
            password_hash = generate_password_hash(password)
            save_auth(username, password_hash)
            session.clear()
            session.permanent = True
            session["authenticated"] = True
            session["username"] = username
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("events.index"))
    return render_template("setup.html", error=error)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate Web UI user with rate limiting and constant-time credential verification."""
    ctx = get_web_ctx()
    if not check_auth_configured_via_hooks(ctx):
        return redirect(url_for("auth.setup"))
    if check_auth_requires_change_via_hooks(ctx):
        return redirect(url_for("auth.setup"))
    error = None
    if request.method == "POST":
        client_key = request.remote_addr or "unknown"
        limited, retry_after = ctx.rate_limiter.is_limited(client_key)
        if limited:
            resp = Response("Too many failed login attempts. Try again later.", 429)
            resp.headers["Retry-After"] = str(retry_after)
            return resp

        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        if not username or not password:
            error = "Username and password are required."
        elif not verify_credentials(username, password):
            ctx.rate_limiter.record_failure(client_key)
            error = "Invalid username or password."
        else:
            ctx.rate_limiter.clear(client_key)
            session.clear()
            session.permanent = True
            session["authenticated"] = True
            session["username"] = username
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("events.index"))
    return render_template("login.html", error=error)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Clear the authenticated Web UI session and redirect or acknowledge JSON clients."""
    session.clear()
    if request.is_json or "application/json" in (request.headers.get("Accept") or ""):
        return jsonify({"ok": True})
    return redirect(url_for("auth.login"))


@auth_bp.route("/api/csrf-token")
def api_csrf_token():
    """Return the active per-session CSRF token for authenticated dashboard tabs."""
    return jsonify({"csrf_token": get_csrf_token()})


@auth_bp.route("/api/credentials/status")
def api_credentials_status():
    """Return whether runtime mailbox credentials are configured in memory."""
    ctx = get_web_ctx()
    return jsonify({"configured": check_credentials_ready_via_hooks(ctx)})


@auth_bp.route("/api/credentials", methods=["POST"])
def api_set_credentials():
    """Update in-memory mailbox credentials via JSON payload."""
    ctx = get_web_ctx()
    if getattr(config, "REQUIRE_HTTPS_FOR_CREDENTIALS", True) and not (is_secure_request() or is_local_request()):
        return jsonify({"error": "TLS required for credential submission"}), 400

    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 415

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid JSON payload"}), 400

    password = (payload.get("password") or "").strip()
    email_address = (payload.get("email_address") or "").strip()
    exchange_username = (payload.get("exchange_username") or "").strip()

    if not password:
        return jsonify({"error": "Password is required"}), 400

    try:
        config.set_email_credentials(
            password,
            email_address=email_address or None,
            exchange_username=exchange_username or None,
        )
    except Exception as exc:
        current_app.logger.warning("Falling back to direct credential assignment: %s", exc)
        config.EMAIL_PASSWORD = password
        if email_address:
            config.EMAIL_ADDRESS = email_address
            config.EXCHANGE_EMAIL = email_address
        if exchange_username:
            config.EXCHANGE_USERNAME = exchange_username

    resolve_runtime(ctx).invalidate_bot()
    return jsonify({"ok": True})


__all__ = ["auth_bp"]

