"""Compatibility facade exposing the modular Web UI app factory, WSGI entrypoints, and hooks."""

from __future__ import annotations

import atexit
from pathlib import Path
import sys
from typing import Any, Dict, Optional

from flask import Flask

import config
from email_client import EmailClient as EmailClient
from exchange_client import ExchangeClient as ExchangeClient
from ical_bot.runtime import BotRuntime, serve_wsgi
from ical_bot.web.app import create_app, prepare_runtime_startup
from ical_bot.web.auth import (
    auth_configured,
    auth_requires_change,
    check_auth_session,
    credentials_ready,
)
from ical_bot.web.state import EXTENSION_KEY
from storage import StorageManager

PROJECT_ROOT = Path(__file__).resolve().parent

storage = StorageManager(config.RESULTS_DB_PATH)
runtime = BotRuntime(storage=storage)


def _check_auth() -> bool:
    """Return whether the current Flask session is authenticated."""
    return check_auth_session()


def _auth_configured() -> bool:
    """Return whether local Web UI operator credentials have been configured."""
    return auth_configured()


def _auth_requires_change() -> bool:
    """Return whether stored Web UI credentials require rotation."""
    return auth_requires_change()


def _credentials_ready() -> bool:
    """Return whether runtime mailbox credentials are available."""
    return credentials_ready()


app: Flask = create_app(
    storage=storage,
    runtime=runtime,
    start_bot=False,
    project_root=PROJECT_ROOT,
    hooks=sys.modules[__name__],
)


def _reset_login_rate_limits() -> None:
    """Reset the active Web UI login rate limiter state."""
    app.extensions[EXTENSION_KEY].rate_limiter.reset()


def _send_calendar_invite_from_record(
    record: Dict[str, Any],
    recipients_override: Optional[Any] = None,
    selected_event_index: Optional[int] = None,
    event_overrides: Optional[Dict[str, Any]] = None,
):
    """Delegate calendar invite composition and dispatch to the active ``EventWorkflow``."""
    return app.extensions[EXTENSION_KEY].workflow.send_calendar_invite_from_record(
        record,
        recipients_override=recipients_override,
        selected_event_index=selected_event_index,
        event_overrides=event_overrides,
    )


def get_stats() -> Dict[str, Any]:
    """Return dashboard metrics combined with runtime and sanitized workload state."""
    return app.extensions[EXTENSION_KEY].workflow.get_stats()


def create_wsgi_app(*, start_bot: bool = True) -> Flask:
    """Prepare startup maintenance, optionally start background polling, and return the WSGI app."""
    prepare_runtime_startup(app)
    if start_bot:
        runtime.start_background_polling()
        atexit.register(runtime.stop)
    return app


def run_web_ui() -> None:
    """Start the production Waitress WSGI server for the Web UI."""
    prepare_runtime_startup(app)
    serve_wsgi(app, config.WEB_UI_HOST, config.WEB_UI_PORT)


if __name__ == "__main__":
    create_wsgi_app(start_bot=True)
    serve_wsgi(app, config.WEB_UI_HOST, config.WEB_UI_PORT)
