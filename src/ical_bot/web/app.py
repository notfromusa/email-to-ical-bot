"""Flask application factory assembling configuration, storage, runtime, workflow, and Blueprints."""

from __future__ import annotations

import atexit
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

import config
from ical_bot.mail.protocol import MailAdapter, create_mail_adapter
from ical_bot.runtime import BotRuntime
from ical_bot.settings import Settings
from ical_bot.web.auth import (
    DynamicSecureCookieSessionInterface,
    LoginRateLimiter,
    ensure_secret_key,
    register_security_hooks,
)
from ical_bot.web.routes_admin import admin_bp
from ical_bot.web.routes_auth import auth_bp
from ical_bot.web.routes_events import events_bp
from ical_bot.web.routes_manual import manual_bp
from ical_bot.web.state import EXTENSION_KEY, AppContext, resolve_runtime, resolve_storage
from ical_bot.web.workflow import EventWorkflow
from storage import StorageManager

# Suppress verbose HTTP request access logs from Werkzeug
logging.getLogger("werkzeug").setLevel(logging.WARNING)


def _default_project_root() -> Path:
    """Walk upwards from this file to locate the directory containing ``templates/``."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "templates").is_dir():
            return parent
    return Path.cwd()


def prepare_runtime_startup(app_obj: Flask) -> None:
    """Ensure secret key, templates directory, initial redaction, and retention purge."""
    ctx = app_obj.extensions[EXTENSION_KEY]
    template_path = Path(app_obj.root_path) / "templates" / "index.html"
    template_path.parent.mkdir(exist_ok=True)
    if not template_path.exists():
        template_path.write_text(ctx.default_template, encoding="utf-8")

    ensure_secret_key(app_obj)
    active_storage = resolve_storage(ctx)
    try:
        result = active_storage.censor_sensitive_fields()
        app_obj.logger.info("Startup censor complete: %s record(s) updated", result.get("records_updated", 0))
    except Exception as exc:
        app_obj.logger.warning("Startup censor failed: %s", exc)
    try:
        purge_result = active_storage.purge_old_entries(days=30)
        app_obj.logger.info("Startup purge complete: %s record(s) deleted", purge_result.get("records_deleted", 0))
    except Exception as exc:
        app_obj.logger.warning("Startup purge failed: %s", exc)


def create_app(
    settings: Optional[Settings] = None,
    storage: Optional[StorageManager] = None,
    runtime: Optional[BotRuntime] = None,
    *,
    start_bot: bool = False,
    project_root: Optional[Path] = None,
    hooks: Optional[Any] = None,
    mail_client_factory: Optional[Callable[[], MailAdapter]] = None,
) -> Flask:
    """Construct and configure an isolated Flask application instance for the Web UI.

    Args:
        settings: Optional typed :class:`~ical_bot.settings.Settings` instance.
        storage: Optional pre-built :class:`~storage.StorageManager`.
        runtime: Optional pre-built :class:`~ical_bot.runtime.BotRuntime`.
        start_bot: When True, runs startup maintenance and starts background polling.
        project_root: Repository root containing ``templates/`` and ``static/``.
        hooks: Optional compatibility module object (e.g., ``web_ui``) honoring live patches.
        mail_client_factory: Optional custom factory returning a :class:`~ical_bot.mail.MailAdapter`.

    Returns:
        Configured :class:`flask.Flask` application.
    """
    resolved_settings = settings or Settings.from_config_module(config)
    root = Path(project_root).resolve() if project_root is not None else _default_project_root()

    app_obj = Flask(
        __name__,
        root_path=str(root),
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    app_obj.session_interface = DynamicSecureCookieSessionInterface()
    app_obj.config["SESSION_COOKIE_HTTPONLY"] = True
    app_obj.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app_obj.config["SESSION_COOKIE_SECURE"] = bool(resolved_settings.session_cookie_secure)
    app_obj.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        hours=max(1, int(resolved_settings.session_lifetime_hours))
    )
    app_obj.config["MAX_CONTENT_LENGTH"] = int(resolved_settings.web_ui_max_content_length)
    app_obj.config["CSRF_ENABLED"] = True

    trusted_proxy_count = max(0, int(resolved_settings.trusted_proxy_count))
    if trusted_proxy_count > 0:
        app_obj.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app_obj.wsgi_app,
            x_for=trusted_proxy_count,
            x_proto=trusted_proxy_count,
            x_host=trusted_proxy_count,
            x_port=trusted_proxy_count,
        )

    ensure_secret_key(app_obj)

    active_storage = storage if storage is not None else StorageManager(resolved_settings.results_db_path)
    active_runtime = runtime if runtime is not None else BotRuntime(storage=active_storage)
    rate_limiter = LoginRateLimiter()

    def _default_mail_factory() -> MailAdapter:
        if hooks is not None:
            proto = getattr(config, "EMAIL_PROTOCOL", "IMAP")
            if proto == "EXCHANGE" and hasattr(hooks, "ExchangeClient"):
                return hooks.ExchangeClient()
            if proto != "EXCHANGE" and hasattr(hooks, "EmailClient"):
                return hooks.EmailClient()
        return create_mail_adapter()

    ctx: AppContext

    def _storage_provider() -> StorageManager:
        return resolve_storage(ctx)

    def _runtime_provider() -> BotRuntime:
        return resolve_runtime(ctx)

    workflow = EventWorkflow(
        storage_provider=_storage_provider,
        runtime_provider=_runtime_provider,
        mail_client_factory=mail_client_factory or _default_mail_factory,
    )

    template_path = root / "templates" / "index.html"
    try:
        default_template = template_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        default_template = "<!DOCTYPE html><html><body><p>Template missing. Please create templates/index.html.</p></body></html>"

    ctx = AppContext(
        storage=active_storage,
        runtime=active_runtime,
        workflow=workflow,
        rate_limiter=rate_limiter,
        settings=resolved_settings,
        hooks=hooks,
        default_template=default_template,
    )
    app_obj.extensions[EXTENSION_KEY] = ctx

    register_security_hooks(app_obj)
    app_obj.register_blueprint(auth_bp)
    app_obj.register_blueprint(events_bp)
    app_obj.register_blueprint(manual_bp)
    app_obj.register_blueprint(admin_bp)

    if start_bot:
        prepare_runtime_startup(app_obj)
        active_runtime.start_background_polling()
        atexit.register(active_runtime.stop)

    return app_obj


__all__ = ["create_app", "prepare_runtime_startup"]

