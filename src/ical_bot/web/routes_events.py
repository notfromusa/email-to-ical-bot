"""Event listing, approval, decline, rescan, preview, and statistics Blueprint routes."""

from __future__ import annotations

import os

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    send_from_directory,
    session,
)

from ical_bot.web.auth import check_credentials_ready_via_hooks
from ical_bot.web.state import get_web_ctx

events_bp = Blueprint("events", __name__)


@events_bp.route("/")
def index():
    """Render the main monitoring dashboard HTML page."""
    return render_template("index.html")


@events_bp.route("/favicon.ico")
def favicon():
    """Serve ``favicon.ico`` from the static directory."""
    return send_from_directory(
        os.path.join(current_app.root_path, "static"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


@events_bp.route("/api/events")
def api_events():
    """Return summarized event records filtered by status/category."""
    ctx = get_web_ctx()
    if not check_credentials_ready_via_hooks(ctx):
        return jsonify({"error": "Mailbox credentials are not configured"}), 409
    filter_type = request.args.get("filter", "all")
    limit_param = request.args.get("limit", "200")
    try:
        limit = int(limit_param)
    except ValueError:
        limit = 200
    return jsonify(ctx.workflow.list_events(filter_type=filter_type, limit=limit))


@events_bp.route("/api/events/<email_id>")
def api_event_detail(email_id: str):
    """Return full details, candidates, and audit history for a single processed email."""
    ctx = get_web_ctx()
    if not check_credentials_ready_via_hooks(ctx):
        return jsonify({"error": "Mailbox credentials are not configured"}), 409
    detail = ctx.workflow.get_event_detail(email_id)
    if detail is None:
        return jsonify({"error": "Event not found"}), 404
    return jsonify(detail)


@events_bp.route("/api/events/<email_id>/approve", methods=["POST"])
def api_approve_event(email_id: str):
    """Approve (or re-send) an event candidate and dispatch calendar invite."""
    ctx = get_web_ctx()
    payload = request.get_json(silent=True) or {} if request.is_json else {}
    approver = str(session.get("username") or "authenticated-user")
    status_code, body = ctx.workflow.approve_event(email_id, approver=approver, payload=payload)
    return jsonify(body), status_code


@events_bp.route("/api/events/<email_id>/decline", methods=["POST"])
def api_decline_event(email_id: str):
    """Decline a pending calendar event without dispatching invites."""
    ctx = get_web_ctx()
    reason = None
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        reason = payload.get("reason")
    actor = str(session.get("username") or "authenticated-user")
    status_code, body = ctx.workflow.decline_event(email_id, actor=actor, reason=reason)
    return jsonify(body), status_code


@events_bp.route("/api/events/<email_id>/rescan", methods=["POST"])
def api_rescan_event(email_id: str):
    """Re-fetch an email from the mailbox and re-run LLM extraction (optionally double-checked)."""
    ctx = get_web_ctx()
    if not check_credentials_ready_via_hooks(ctx):
        return jsonify({"error": "Mailbox credentials are not configured"}), 409
    payload = request.get_json(silent=True) or {} if request.is_json else {}
    double_check = bool(payload.get("double_check", False))
    actor = str(session.get("username") or "authenticated-user")
    rescan_hint = payload.get("hint") or payload.get("notes") or payload.get("rescan_hint")
    status_code, body = ctx.workflow.rescan_event(
        email_id,
        actor=actor,
        double_check=double_check,
        rescan_hint=rescan_hint,
    )
    return jsonify(body), status_code


@events_bp.route("/api/events/<email_id>/preview-ics", methods=["POST"])
def api_preview_ics(email_id: str):
    """Generate live ICS preview text reflecting UI edits and selected recipients."""
    ctx = get_web_ctx()
    payload = request.get_json(silent=True) or {} if request.is_json else {}
    status_code, body = ctx.workflow.preview_ics(
        email_id,
        selected_event_index=payload.get("selected_event_index"),
        event_overrides=payload.get("event_overrides"),
        recipients_override=payload.get("recipients"),
    )
    return jsonify(body), status_code


@events_bp.route("/api/stats")
def api_stats():
    """Return dashboard statistics and sanitized workload state."""
    ctx = get_web_ctx()
    if not check_credentials_ready_via_hooks(ctx):
        return jsonify({"error": "Mailbox credentials are not configured"}), 409
    return jsonify(ctx.workflow.get_stats())


__all__ = ["events_bp"]

