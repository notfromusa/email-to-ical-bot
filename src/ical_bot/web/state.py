"""Shared per-application dependency container for the Web UI package."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from flask import Flask, current_app

if TYPE_CHECKING:
    from ical_bot.runtime import BotRuntime
    from ical_bot.settings import Settings
    from ical_bot.web.auth import LoginRateLimiter
    from ical_bot.web.workflow import EventWorkflow
    from storage import StorageManager

EXTENSION_KEY = "ical_bot"
EPOCH_FALLBACK = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass
class AppContext:
    """Explicit dependency container attached to ``app.extensions['ical_bot']``."""

    storage: "StorageManager"
    runtime: "BotRuntime"
    workflow: "EventWorkflow"
    rate_limiter: "LoginRateLimiter"
    settings: Optional["Settings"] = None
    hooks: Optional[Any] = None
    auth_warning_logged: bool = False
    default_template: str = field(
        default="<!DOCTYPE html><html><body><p>Template missing. Please create templates/index.html.</p></body></html>"
    )


def get_web_ctx(app_obj: Optional[Flask] = None) -> AppContext:
    """Retrieve the :class:`AppContext` attached to the active or supplied Flask app."""
    target = app_obj if app_obj is not None else current_app
    ctx = target.extensions.get(EXTENSION_KEY)
    if not isinstance(ctx, AppContext):
        raise RuntimeError("iCal Bot Flask extension context is not initialized")
    return ctx


def resolve_storage(ctx: AppContext) -> "StorageManager":
    """Return the active ``StorageManager``, respecting compatibility hook overrides."""
    if ctx.hooks is not None and hasattr(ctx.hooks, "storage"):
        return ctx.hooks.storage
    return ctx.storage


def resolve_runtime(ctx: AppContext) -> "BotRuntime":
    """Return the active ``BotRuntime``, respecting compatibility hook overrides."""
    if ctx.hooks is not None and hasattr(ctx.hooks, "runtime"):
        return ctx.hooks.runtime
    return ctx.runtime


__all__ = [
    "AppContext",
    "EPOCH_FALLBACK",
    "EXTENSION_KEY",
    "get_web_ctx",
    "resolve_runtime",
    "resolve_storage",
]

