"""Modular Flask Web UI package with app factory, Blueprints, auth middleware, and workflows."""

from ical_bot.web.app import create_app, prepare_runtime_startup
from ical_bot.web.workflow import EventWorkflow

__all__ = ["EventWorkflow", "create_app", "prepare_runtime_startup"]

