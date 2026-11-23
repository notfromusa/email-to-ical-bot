"""Workload monitor, configuration toggle, and database maintenance Blueprint routes."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

import config
from ical_bot.web.state import get_web_ctx, resolve_storage

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/api/workload-status")
def api_workload_status():
    """Return workstation GPU/CPU workload status with PIDs and command names redacted."""
    from workload_monitor import get_workload_monitor

    return jsonify(get_workload_monitor().get_status(include_process_details=False))


@admin_bp.route("/api/workload/unload", methods=["POST"])
def api_workload_unload():
    """Explicitly unload the active Ollama LLM model from VRAM."""
    from workload_monitor import get_workload_monitor

    monitor = get_workload_monitor()
    success = monitor.unload_model()
    return jsonify(
        {
            "ok": success,
            "status": monitor.get_status(include_process_details=False),
        }
    )


@admin_bp.route("/api/config")
def api_config():
    """Return non-sensitive runtime dashboard configuration fields."""
    return jsonify(
        {
            "dry_run": config.DRY_RUN,
            "check_interval": config.CHECK_INTERVAL_SECONDS,
            "max_emails_per_check": config.MAX_EMAILS_PER_CHECK,
            "llm_model": config.LLM_MODEL,
            "email_address": config.EMAIL_ADDRESS,
            "internal_domain": getattr(config, "INTERNAL_EMAIL_DOMAIN", None),
            "attachment_parsing_enabled": bool(getattr(config, "ATTACHMENT_PARSING_ENABLED", True)),
        }
    )


@admin_bp.route("/api/config/attachment-parsing", methods=["POST"])
def api_set_attachment_parsing():
    """Toggle attachment text extraction on or off and persist UI preference."""
    payload = request.get_json(silent=True) or {}
    if "enabled" not in payload:
        return jsonify({"error": "Missing enabled flag"}), 400

    enabled = bool(payload.get("enabled"))
    config.set_attachment_parsing_enabled(enabled, persist=True)
    current_app.logger.info("Attachment parsing %s via UI", "enabled" if enabled else "disabled")
    return jsonify({"ok": True, "attachment_parsing_enabled": bool(config.ATTACHMENT_PARSING_ENABLED)})


@admin_bp.route("/api/database/censor", methods=["POST"])
def api_censor_database():
    """Redact stored prompts/bodies and vacuum freed SQLite pages."""
    ctx = get_web_ctx()
    try:
        result = resolve_storage(ctx).censor_sensitive_fields()
        return jsonify({"ok": True, **result})
    except Exception as exc:
        current_app.logger.error("Database censoring failed: %s", exc)
        return jsonify({"error": "Failed to censor database."}), 500


@admin_bp.route("/api/database/purge", methods=["POST"])
def api_purge_database():
    """Purge processed records older than 7 days and archive their IDs."""
    ctx = get_web_ctx()
    try:
        result = resolve_storage(ctx).purge_old_entries(days=7)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        current_app.logger.error("Database purge failed: %s", exc)
        return jsonify({"error": "Failed to purge database."}), 500


@admin_bp.route("/api/database/repair", methods=["POST"])
def api_repair_database():
    """Reconcile inconsistent approval statuses across ``processed_results`` and ``approval_logs``."""
    ctx = get_web_ctx()
    try:
        result = resolve_storage(ctx).reconcile_approvals()
        return jsonify({"ok": True, **result})
    except Exception as exc:
        current_app.logger.error("Database repair failed: %s", exc)
        return jsonify({"error": "Failed to repair database."}), 500


__all__ = ["admin_bp"]

