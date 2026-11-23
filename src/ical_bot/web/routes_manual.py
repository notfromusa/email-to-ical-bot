"""Manual scan trigger and ad-hoc email evaluation Blueprint routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ical_bot.web.auth import check_credentials_ready_via_hooks
from ical_bot.web.state import get_web_ctx, resolve_runtime

manual_bp = Blueprint("manual", __name__)


@manual_bp.route("/api/check-now", methods=["POST"])
def api_check_now():
    """Trigger an asynchronous manual inbox check via ``BotRuntime``."""
    ctx = get_web_ctx()
    if not check_credentials_ready_via_hooks(ctx):
        return jsonify({"error": "Mailbox credentials are not configured"}), 409
    ok, error = resolve_runtime(ctx).trigger_manual_check()
    if not ok:
        return jsonify({"error": error}), 409
    return jsonify({"ok": True})


@manual_bp.route("/api/manual-evaluate", methods=["POST"])
def api_manual_evaluate():
    """Run ephemeral LLM extraction and ICS generation on pasted email text."""
    ctx = get_web_ctx()
    payload = request.get_json(silent=True) or {}
    subject = (payload.get("subject") or "").strip() or "(Manual evaluation)"
    body = (payload.get("body") or "").strip()
    reference_timestamp = (payload.get("reference_timestamp") or "").strip() or None
    status_code, result = ctx.workflow.manual_evaluate(
        subject=subject,
        body=body,
        reference_timestamp=reference_timestamp,
    )
    return jsonify(result), status_code


@manual_bp.route("/api/manual-evaluate/send-self", methods=["POST"])
def api_manual_evaluate_send_self():
    """Regenerate ICS server-side from validated event fields and send invite to self."""
    ctx = get_web_ctx()
    if not check_credentials_ready_via_hooks(ctx):
        return jsonify({"error": "Mailbox credentials are not configured"}), 409
    payload = request.get_json(silent=True) or {}
    subject = (payload.get("subject") or "").strip() or "(Manual evaluation)"
    selected_event = payload.get("selected_event")
    event_payload = selected_event if isinstance(selected_event, dict) else payload.get("event")
    status_code, result = ctx.workflow.manual_evaluate_send_self(
        subject=subject,
        event_payload=event_payload,
    )
    return jsonify(result), status_code


__all__ = ["manual_bp"]

