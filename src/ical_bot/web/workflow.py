"""HTTP-independent workflow module for event normalization, approval, rescan, and evaluation."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import config
from calendar_generator import CalendarGenerator
from ical_bot.mail.protocol import MailAdapter, create_mail_adapter
from ical_bot.runtime import BotRuntime
from ical_bot.time_utils import get_default_timezone, parse_iso_datetime, utc_stamp
from ical_bot.web.state import EPOCH_FALLBACK
from llm_client import LLMClient
from main import iCalBot
from messaging import build_reply_message
from storage import StorageManager

logger = logging.getLogger(__name__)

SUMMARY_HIDDEN_FIELDS = frozenset(
    {
        "email_body",
        "ics_content",
        "llm_prompt",
        "llm_response",
        "llm_raw_result",
    }
)


def decode_recipient_list(raw: Any) -> List[str]:
    """Decode a stored JSON string or list into a deduplicated list of address strings."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        parsed = raw
    if isinstance(parsed, list):
        recipients: List[str] = []
        for item in parsed:
            addr: Optional[str]
            if isinstance(item, dict):
                addr = item.get("email") or item.get("address")
            else:
                addr = str(item)
            addr_str = (addr or "").strip()
            if addr_str and addr_str not in recipients:
                recipients.append(addr_str)
        return recipients
    addr_str = str(raw).strip()
    return [addr_str] if addr_str else []


def decode_event_candidates(raw: Any) -> List[Dict[str, str]]:
    """Decode persisted JSON event candidate lists into normalized candidate dicts."""
    if raw is None:
        return []
    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(parsed, list):
        return []

    candidates: List[Dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        candidate = {
            "title": str(item.get("title") or "Untitled event"),
            "description": str(item.get("description") or ""),
            "start_datetime": str(item.get("start_datetime") or ""),
            "end_datetime": str(item.get("end_datetime") or ""),
            "location": str(item.get("location") or ""),
        }
        if not candidate["start_datetime"]:
            continue
        candidates.append(candidate)
    return candidates


def event_candidates_from_event_data(event_data: Any) -> List[Dict[str, str]]:
    """Extract normalized event candidates from an LLM extraction payload dictionary."""
    if not isinstance(event_data, dict):
        return []
    raw_candidates = event_data.get("events")
    if not isinstance(raw_candidates, list):
        raw_candidates = []

    normalized: List[Dict[str, str]] = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        candidate = {
            "title": str(item.get("title") or "Untitled event"),
            "description": str(item.get("description") or ""),
            "start_datetime": str(item.get("start_datetime") or ""),
            "end_datetime": str(item.get("end_datetime") or ""),
            "location": str(item.get("location") or ""),
        }
        if not candidate["start_datetime"]:
            continue
        normalized.append(candidate)

    if normalized:
        return normalized

    single = {
        "title": str(event_data.get("title") or "Untitled event"),
        "description": str(event_data.get("description") or ""),
        "start_datetime": str(event_data.get("start_datetime") or ""),
        "end_datetime": str(event_data.get("end_datetime") or ""),
        "location": str(event_data.get("location") or ""),
    }
    if single["start_datetime"]:
        return [single]
    return []


def resolve_event_datetime(record: Optional[Dict[str, Any]]) -> Optional[datetime]:
    """Select the primary chronological UTC datetime representing a stored record."""
    if not record:
        return None
    default_tz = get_default_timezone()
    for key in ("email_date", "processed_at", "event_start", "approval_timestamp"):
        dt = parse_iso_datetime(record.get(key), default_tz=default_tz, as_utc=True)
        if dt is not None:
            return dt
    return None


def normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize SQLite row fields (booleans, JSON lists, indices) for API responses."""
    normalized = dict(record)
    for key in ("has_event", "sent", "dry_run", "approval_required", "prompt_injection"):
        if key in normalized:
            normalized[key] = bool(normalized[key])

    candidates = decode_event_candidates(normalized.get("event_candidates"))
    if not candidates and normalized.get("has_event"):
        fallback = {
            "title": str(normalized.get("event_title") or "Untitled event"),
            "description": str(normalized.get("event_description") or ""),
            "start_datetime": str(normalized.get("event_start") or ""),
            "end_datetime": str(normalized.get("event_end") or ""),
            "location": str(normalized.get("event_location") or ""),
        }
        if fallback["start_datetime"]:
            candidates = [fallback]
    normalized["event_candidates"] = candidates

    selected_raw = normalized.get("selected_event_index")
    try:
        selected_index = int(selected_raw) if selected_raw is not None else 0
    except (TypeError, ValueError):
        selected_index = 0
    if selected_index < 0 or (candidates and selected_index >= len(candidates)):
        selected_index = 0
    normalized["selected_event_index"] = selected_index
    normalized["email_recipients"] = decode_recipient_list(normalized.get("email_recipients"))

    sent_indices_raw = normalized.get("sent_event_indices")
    if isinstance(sent_indices_raw, str):
        try:
            parsed_indices = json.loads(sent_indices_raw)
            normalized["sent_event_indices"] = parsed_indices if isinstance(parsed_indices, list) else []
        except Exception:
            normalized["sent_event_indices"] = []
    elif isinstance(sent_indices_raw, list):
        normalized["sent_event_indices"] = sent_indices_raw
    else:
        normalized["sent_event_indices"] = []
    return normalized


def build_event_payload(
    record: Dict[str, Any],
    selected_event_index: Optional[int] = None,
) -> Tuple[Dict[str, str], int]:
    """Select the event candidate dictionary and resolved candidate index for a record."""
    candidates = decode_event_candidates(record.get("event_candidates"))
    if not candidates:
        return (
            {
                "title": str(record.get("event_title") or "Untitled event"),
                "description": str(record.get("event_description") or ""),
                "start_datetime": str(record.get("event_start") or ""),
                "end_datetime": str(record.get("event_end") or ""),
                "location": str(record.get("event_location") or ""),
            },
            0,
        )

    target = record.get("selected_event_index") if selected_event_index is None else selected_event_index
    try:
        index = int(target) if target is not None else 0
    except (TypeError, ValueError):
        index = 0
    if index < 0 or index >= len(candidates):
        index = 0
    return dict(candidates[index]), index


def format_send_result_note(result: Optional[Dict[str, Any]]) -> Optional[str]:
    """Format a concise human-readable audit log note describing how an invite was dispatched."""
    if not result:
        return None
    parts: List[str] = []
    if result.get("direct_calendar_item"):
        parts.append("Saved directly to Exchange calendar (editable)")
    recipients = result.get("recipients") or []
    if recipients:
        parts.append(f"Recipients: {', '.join(recipients)}")
    protocol = result.get("protocol")
    if protocol:
        parts.append(f"Protocol: {protocol}")
    cal_id = result.get("calendar_item_id")
    if cal_id:
        parts.append(f"CalendarItem-ID: {cal_id}")
    message_id = result.get("message_id")
    if message_id:
        parts.append(f"Message-ID: {message_id}")
    return " | ".join(parts) if parts else None


class EventWorkflow:
    """Deep application workflow module orchestrating Web UI operations over storage and adapters."""

    def __init__(
        self,
        *,
        storage_provider: Callable[[], StorageManager],
        runtime_provider: Callable[[], BotRuntime],
        mail_client_factory: Optional[Callable[[], MailAdapter]] = None,
    ) -> None:
        """Initialize the workflow with explicit provider seams for storage, runtime, and mail."""
        self._storage_provider = storage_provider
        self._runtime_provider = runtime_provider
        self._mail_client_factory = mail_client_factory or create_mail_adapter

    @property
    def storage(self) -> StorageManager:
        """Return the active :class:`~storage.StorageManager`."""
        return self._storage_provider()

    @property
    def runtime(self) -> BotRuntime:
        """Return the active :class:`~ical_bot.runtime.BotRuntime`."""
        return self._runtime_provider()

    def _create_mail_client(self) -> MailAdapter:
        return self._mail_client_factory()

    @staticmethod
    def summarize_record(record: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[datetime]]:
        """Produce a lightweight summary record suitable for event feed responses."""
        data = normalize_record(record)
        for field in SUMMARY_HIDDEN_FIELDS:
            data.pop(field, None)
        event_dt = resolve_event_datetime(data)
        data["email_timestamp"] = event_dt.isoformat() if event_dt else None
        return data, event_dt

    def detailed_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Produce a full detail record including decoded raw LLM output and approval log."""
        data = normalize_record(record)
        raw_payload = data.get("llm_raw_result")
        if isinstance(raw_payload, str):
            try:
                data["llm_raw_result"] = json.loads(raw_payload)
            except json.JSONDecodeError:
                pass
        email_id = record.get("email_id")
        data["approval_history"] = self.storage.get_approval_history(email_id) if email_id else []
        return data

    def list_events(self, *, filter_type: str = "all", limit: int = 200) -> List[Dict[str, Any]]:
        """List summarized processed records sorted appropriately for ``filter_type``."""
        bounded_limit = max(1, min(int(limit), 500))
        summarized = [self.summarize_record(entry) for entry in self.storage.get_results(filter_type, bounded_limit)]
        summarized.sort(
            key=lambda item: item[1] or EPOCH_FALLBACK,
            reverse=(filter_type != "pending"),
        )
        return [item[0] for item in summarized]

    def get_event_detail(self, email_id: str) -> Optional[Dict[str, Any]]:
        """Return the normalized detailed record for ``email_id``, or None if missing."""
        record = self.storage.get_result(email_id)
        if not record:
            return None
        return self.detailed_record(record)

    def send_calendar_invite_from_record(
        self,
        record: Dict[str, Any],
        recipients_override: Optional[Any] = None,
        selected_event_index: Optional[int] = None,
        event_overrides: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, str], int, str]:
        """Compose/regenerate an ICS invite from ``record`` and send it via the active mail adapter."""
        mail_client = self._create_mail_client()
        recipients = (
            decode_recipient_list(recipients_override)
            if recipients_override
            else decode_recipient_list(record.get("email_recipients"))
        )
        if not recipients and record.get("email_sender"):
            recipients = [str(record["email_sender"])]
        if not recipients:
            raise ValueError("No recipients available for this event")

        original_email: Dict[str, Any] = {
            "id": record.get("email_id"),
            "subject": record.get("email_subject"),
            "sender": record.get("email_sender"),
            "message_id": record.get("email_message_id") or record.get("email_id"),
            "recipients": recipients,
            "calendar_item_id": record.get("calendar_item_id"),
        }
        if hasattr(mail_client, "get_message_by_id") and callable(mail_client.get_message_by_id):
            original_msg = mail_client.get_message_by_id(original_email.get("message_id"))
            if original_msg:
                original_email["original_msg"] = original_msg

        event_payload, resolved_index = build_event_payload(record, selected_event_index=selected_event_index)
        if event_overrides and isinstance(event_overrides, dict):
            for field in ("title", "description", "start_datetime", "end_datetime", "location"):
                if event_overrides.get(field) is not None:
                    event_payload[field] = str(event_overrides[field]).strip()
        reply_message = build_reply_message(event_payload, recipients)

        generator = CalendarGenerator()
        ics_content = record.get("ics_content")
        try:
            ics_content = generator.create_ics_invite(event_payload, recipients=recipients)
        except Exception as exc:
            logger.warning(
                "Failed to regenerate ICS for %s: %s; using stored content",
                record.get("email_id"),
                exc,
            )
            if not ics_content:
                raise
        if not ics_content:
            raise ValueError("Missing ICS content for this event")
        original_email["event_title"] = event_payload.get("title")

        send_result = mail_client.send_reply_with_calendar(
            original_email,
            ics_content,
            reply_message,
            dry_run=config.DRY_RUN,
            event_data=event_payload,
            event_title=event_payload.get("title"),
        )
        return send_result, event_payload, resolved_index, ics_content

    def approve_event(
        self,
        email_id: str,
        *,
        approver: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """Validate, send, and record approval of a pending or already-sent event."""
        record = self.storage.get_result(email_id)
        if not record:
            return 404, {"error": "Event not found"}
        if not record.get("has_event"):
            return 400, {"error": "No event data stored for this email"}
        if not record.get("approval_required"):
            return 400, {"error": "This event does not require approval"}

        available_candidates = decode_event_candidates(record.get("event_candidates"))
        sent_indices = record.get("sent_event_indices") or []
        if isinstance(sent_indices, str):
            try:
                sent_indices = json.loads(sent_indices)
            except Exception:
                sent_indices = []

        req_payload = payload or {}
        selected_event_index = req_payload.get("selected_event_index")
        if selected_event_index is not None:
            try:
                selected_event_index = int(selected_event_index)
            except (TypeError, ValueError):
                return 400, {"error": "selected_event_index must be an integer"}

        if len(available_candidates) > 1 and selected_event_index is None:
            for idx in range(len(available_candidates)):
                if idx not in sent_indices:
                    selected_event_index = idx
                    break
            if selected_event_index is None:
                selected_event_index = 0

        is_resend = bool(record.get("sent")) or (
            selected_event_index is not None and selected_event_index in sent_indices
        )

        try:
            send_result, selected_event, resolved_index, ics_content = self.send_calendar_invite_from_record(
                record,
                recipients_override=req_payload.get("recipients"),
                selected_event_index=selected_event_index,
                event_overrides=req_payload.get("event_overrides"),
            )
            if available_candidates and resolved_index < len(available_candidates):
                available_candidates[resolved_index] = dict(selected_event)
            note = format_send_result_note(send_result)
            if is_resend:
                note = f"Re-sent: {note}" if note else "Re-sent invite"
            self.storage.mark_event_sent(
                email_id,
                status="dry_run_preview" if config.DRY_RUN else "invite_sent",
                sent=not config.DRY_RUN,
                approver=approver,
                note=note,
                event_data=selected_event,
                event_candidates=available_candidates or None,
                selected_event_index=resolved_index,
                ics_content=ics_content,
                email_recipients=send_result.get("recipients") if send_result else None,
                action="resent" if is_resend else None,
                calendar_item_id=send_result.get("calendar_item_id") if send_result else None,
            )
            action_verb = "Re-sent" if is_resend else "Approved and sent"
            if send_result and send_result.get("recipients"):
                logger.info(
                    "%s invite for %s to %s",
                    action_verb,
                    email_id,
                    ", ".join(send_result["recipients"]),
                )
            else:
                logger.info("%s invite for %s", action_verb, email_id)
            return 200, {"ok": True, "send_result": send_result, "is_resend": is_resend}
        except Exception as exc:
            self.storage.mark_event_sent(
                email_id,
                status="send_error",
                sent=False,
                approver=approver,
                error="Failed to send invite. Check server logs for details.",
            )
            logger.error("Failed to approve event %s: %s", email_id, exc)
            return 500, {"error": "Failed to send invite. Check server logs for details."}

    def decline_event(
        self,
        email_id: str,
        *,
        actor: str,
        reason: Optional[str] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """Decline a pending event so no invite will be sent."""
        record = self.storage.get_result(email_id)
        if not record:
            return 404, {"error": "Event not found"}
        if not record.get("has_event"):
            return 400, {"error": "No event data stored for this email"}
        if not record.get("approval_required"):
            return 400, {"error": "This event does not require approval"}
        if record.get("approval_status") == "declined":
            return 400, {"error": "This event has already been declined"}
        if record.get("approval_status") == "approved":
            return 400, {"error": "This event has already been approved"}

        self.storage.mark_event_declined(email_id, actor=actor, note=reason)
        logger.info("Declined event %s", email_id)
        return 200, {"ok": True}

    def rescan_event(
        self,
        email_id: str,
        *,
        actor: str,
        double_check: bool = False,
        rescan_hint: Optional[str] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """Re-fetch an email from the mailbox and re-run LLM event extraction."""
        record = self.storage.get_result(email_id)
        if not record:
            return 404, {"error": "Event not found"}

        message_id = record.get("email_message_id")
        subject = record.get("email_subject")
        sender = record.get("email_sender")

        mail_client = self._create_mail_client()
        try:
            email_data = mail_client.fetch_email_by_id(
                email_id=email_id,
                message_id=message_id,
                subject=subject,
                sender=sender,
            )
        except Exception as exc:
            logger.error("Error fetching email %s from mailbox: %s", email_id, exc)
            return 500, {"error": "Failed to retrieve email from mailbox."}

        if not email_data:
            return (
                404,
                {
                    "error": (
                        f"Could not locate this email in your mailbox inbox (ID: {email_id}). "
                        "It may have been moved, deleted, or is older than the scan window."
                    )
                },
            )

        try:
            llm_client = LLMClient()
            event_data, metadata = llm_client.analyze_email_for_event(
                email_data["body"],
                email_data["subject"],
                reference_timestamp=email_data.get("date"),
                double_check=double_check,
                is_rescan=True,
                rescan_hint=rescan_hint,
            )
        except Exception as exc:
            logger.error("LLM extraction failed during rescan for %s: %s", email_id, exc)
            return 500, {"error": "LLM extraction failed."}

        candidates = event_candidates_from_event_data(event_data)
        has_event = bool(candidates)
        calendar_generator = CalendarGenerator()
        ics_preview = None
        ics_content = None

        if has_event:
            try:
                recipients = email_data.get("recipients") or (
                    [email_data["sender"]] if email_data.get("sender") else None
                )
                ics_content = calendar_generator.create_ics_invite(candidates[0], recipients=recipients)
                if ics_content:
                    ics_preview = ics_content[:500] + "..." if len(ics_content) > 500 else ics_content
            except Exception as exc:
                logger.warning("Failed to generate ICS during rescan for %s: %s", email_id, exc)

        self.storage.reset_for_rescan(email_id)
        self.storage.record_result(
            email_id=email_id,
            email_message_id=email_data.get("message_id") or message_id,
            email_subject=email_data.get("subject") or record.get("email_subject") or "unknown",
            email_sender=email_data.get("sender") or record.get("email_sender"),
            email_date=email_data.get("date") or record.get("email_date"),
            has_event=has_event,
            event_data=candidates[0] if candidates else None,
            event_candidates=candidates or None,
            selected_event_index=0 if candidates else None,
            ics_preview=ics_preview,
            ics_content=ics_content,
            sent=False,
            dry_run=config.DRY_RUN,
            status="pending_approval" if has_event else "no_event",
            approval_required=True if has_event else False,
            approval_status="pending" if has_event else None,
            email_recipients=email_data.get("recipients") or record.get("email_recipients"),
            llm_prompt=StorageManager._redact_prompt_body(metadata.get("prompt")),
            llm_response=metadata.get("response_text"),
            llm_raw_result=metadata.get("raw_result"),
            prompt_injection=metadata.get("prompt_injection", False),
        )

        note_text = "Rescanned email from mailbox"
        if double_check:
            note_text += " with double-check QA loop"
        if rescan_hint:
            note_text += f" (hint: {rescan_hint[:50]})"
        self.storage.log_approval_action(email_id, "rescanned", actor=actor, note=note_text)

        updated_record = self.storage.get_result(email_id)
        return (
            200,
            {
                "ok": True,
                "record": self.detailed_record(updated_record) if updated_record else None,
                "message": f"Email successfully rescanned{' with double-check' if double_check else ''}.",
            },
        )

    def preview_ics(
        self,
        email_id: str,
        *,
        selected_event_index: Optional[Any] = None,
        event_overrides: Optional[Dict[str, Any]] = None,
        recipients_override: Optional[Any] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """Generate an updated ICS preview using live UI edits and recipient selections."""
        record = self.storage.get_result(email_id)
        if not record:
            return 404, {"error": "Event not found"}

        recipients = (
            decode_recipient_list(recipients_override)
            if recipients_override
            else decode_recipient_list(record.get("email_recipients"))
        )
        if not recipients and record.get("email_sender"):
            recipients = [str(record["email_sender"])]

        event_payload, _ = build_event_payload(record, selected_event_index=selected_event_index)
        if event_overrides and isinstance(event_overrides, dict):
            for field in ("title", "description", "start_datetime", "end_datetime", "location"):
                if event_overrides.get(field) is not None:
                    event_payload[field] = str(event_overrides[field]).strip()

        generator = CalendarGenerator()
        try:
            ics_content = generator.create_ics_invite(event_payload, recipients=recipients)
            return 200, {"ok": True, "ics_content": ics_content}
        except Exception as exc:
            logger.warning("Failed to generate ICS preview for %s: %s", email_id, exc)
            return 400, {"error": "Invalid event parameters for ICS generation."}

    def get_stats(self) -> Dict[str, Any]:
        """Compute dashboard metrics combined with runtime and sanitized workload status."""
        stats = self.storage.get_stats()
        rt_status = self.runtime.get_status()
        stats.update(
            {
                "dry_run_mode": config.DRY_RUN,
                "check_interval": config.CHECK_INTERVAL_SECONDS,
                "bot_running": rt_status["running"],
                "auto_approve_mode": config.AUTO_APPROVE_EVENTS,
                "manual_check_running": rt_status["running"],
                "last_manual_check": rt_status["last_check"],
                "manual_check_error": rt_status["last_error"],
            }
        )
        stats.setdefault("total_events", stats.get("events_detected", 0))
        try:
            from workload_monitor import get_workload_monitor

            stats["workload"] = get_workload_monitor().get_status(include_process_details=False)
        except Exception as exc:
            logger.warning("Failed to get workload status for stats: %s", exc)
            stats["workload"] = {"error": "Workload status unavailable"}
        return stats

    def manual_evaluate(
        self,
        *,
        subject: str,
        body: str,
        reference_timestamp: Optional[str] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """Run one-off LLM extraction and ICS preview on user-supplied text without DB writes."""
        if not body:
            return 400, {"error": "Email text is required"}

        llm_client = LLMClient()
        generator = CalendarGenerator()
        try:
            event_data, metadata = llm_client.analyze_email_for_event(
                body,
                subject,
                reference_timestamp=reference_timestamp,
            )
        except Exception as exc:
            logger.error("LLM evaluation failed during manual eval: %s", exc)
            return (
                502,
                {
                    "ok": False,
                    "error": "LLM evaluation failed.",
                    "has_event": False,
                    "events": [],
                    "llm_prompt": None,
                    "llm_response": None,
                },
            )

        candidates = event_candidates_from_event_data(event_data or {})
        response_payload: Dict[str, Any] = {
            "ok": True,
            "subject": subject,
            "has_event": bool(event_data and event_data.get("has_event") and candidates),
            "events": candidates,
            "selected_event": None,
            "selected_event_index": None,
            "ics_content": None,
            "ics_error": None,
            "llm_prompt": StorageManager._redact_prompt_body(metadata.get("prompt")),
            "llm_response": metadata.get("response_text"),
            "llm_raw_result": iCalBot._sanitize_llm_payload(metadata.get("raw_result")),
            "prompt_injection": bool(metadata.get("prompt_injection")),
        }

        if not response_payload["has_event"]:
            return 200, response_payload

        selected_event = None
        selected_event_index = None
        for idx, candidate in enumerate(candidates):
            if generator.validate_event_data(candidate):
                selected_event = candidate
                selected_event_index = idx
                break

        if not selected_event:
            response_payload["ics_error"] = "No valid future event candidate available for ICS generation."
            return 200, response_payload

        response_payload["selected_event"] = selected_event
        response_payload["selected_event_index"] = selected_event_index

        recipients: List[str] = []
        self_address = (getattr(config, "EMAIL_ADDRESS", "") or "").strip()
        if self_address:
            recipients.append(self_address)

        try:
            response_payload["ics_content"] = generator.create_ics_invite(
                selected_event,
                recipients=recipients or None,
            )
        except Exception as exc:
            logger.warning("ICS generation failed during manual eval: %s", exc)
            response_payload["ics_error"] = "Failed to generate ICS preview."

        return 200, response_payload

    def manual_evaluate_send_self(
        self,
        *,
        subject: str,
        event_payload: Any,
    ) -> Tuple[int, Dict[str, Any]]:
        """Regenerate ICS server-side from validated event fields and send invite to self."""
        self_address = (getattr(config, "EMAIL_ADDRESS", "") or "").strip()
        if not self_address:
            return 400, {"error": "Configured EMAIL_ADDRESS is required"}

        if not isinstance(event_payload, dict):
            return 400, {"error": "Selected event data is required"}

        normalized_event = {
            "title": str(event_payload.get("title") or subject).strip() or subject,
            "description": str(event_payload.get("description") or "").strip(),
            "start_datetime": str(event_payload.get("start_datetime") or "").strip(),
            "end_datetime": str(event_payload.get("end_datetime") or "").strip(),
            "location": str(event_payload.get("location") or "").strip(),
        }
        if not normalized_event["start_datetime"] or not normalized_event["end_datetime"]:
            return 400, {"error": "Event start_datetime and end_datetime are required"}

        generator = CalendarGenerator()
        try:
            ics_content = generator.create_ics_invite(normalized_event, recipients=[self_address])
        except Exception as exc:
            logger.warning("Failed to regenerate manual evaluation ICS: %s", exc)
            return 400, {"error": "Invalid event parameters for ICS generation"}

        reply_message = build_reply_message(normalized_event, [self_address])
        mail_client = self._create_mail_client()
        original_email = {
            "id": f"manual-eval-{utc_stamp('%Y%m%d%H%M%S%f')}",
            "subject": subject,
            "sender": self_address,
            "message_id": None,
            "recipients": [self_address],
            "event_title": normalized_event.get("title"),
        }

        try:
            send_result = mail_client.send_reply_with_calendar(
                original_email,
                ics_content,
                reply_message,
                dry_run=config.DRY_RUN,
                event_data=normalized_event,
                event_title=normalized_event.get("title"),
            )
            return 200, {"ok": True, "send_result": send_result}
        except Exception as exc:
            logger.error("Failed to send manual evaluation ICS to self: %s", exc)
            return 500, {"error": "Failed to send invite."}


__all__ = [
    "EventWorkflow",
    "SUMMARY_HIDDEN_FIELDS",
    "build_event_payload",
    "decode_event_candidates",
    "decode_recipient_list",
    "event_candidates_from_event_data",
    "format_send_result_note",
    "normalize_record",
    "resolve_event_datetime",
]

