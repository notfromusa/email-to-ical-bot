"""Shared email reply-trail cleaning, ICS parsing, recipient filtering, and MIME composition."""

from __future__ import annotations

import logging
import os
import re
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from ical_bot.time_utils import utc_stamp
from identifiers import is_forwarded_email
from messaging import build_reply_message_html_from_text, format_invite_subject

logger = logging.getLogger(__name__)

_EMAIL_ADDRESS_RE = re.compile(r"^[^@\s\x00-\x1f\x7f]+@[^@\s\x00-\x1f\x7f]+\.[^@\s\x00-\x1f\x7f]+$")

_REPLY_MARKERS = (
    "-----original message-----",
    "----- reply message -----",
    "-----ursprüngliche nachricht-----",
)
_HEADER_BLOCK_PREFIXES = (
    "from:",
    "de:",
    "von:",
    "gesendet:",
    "sent:",
    "to:",
    "an:",
    "subject:",
    "betreff:",
    "cc:",
)
_REPLY_HEADER_PATTERNS = (
    re.compile(r"^on\s.+wrote:\s*$", re.IGNORECASE),
    re.compile(r"^am\s.+schrieb.+:\s*$", re.IGNORECASE),
)


def strip_reply_trail(body: str, subject: Optional[str] = None) -> str:
    """Remove quoted previous messages and reply headers while keeping forwards intact.

    Args:
        body: Raw plain-text email body.
        subject: Optional subject line used to detect forwarded messages.

    Returns:
        Trimmed body string preserving primary message and forwarded payloads.
    """
    if not body:
        return body
    if is_forwarded_email(subject=subject, body=body):
        return body.strip()

    lines = body.splitlines()
    for idx, line in enumerate(lines):
        trimmed = line.strip().lower()
        if not trimmed:
            continue
        if trimmed.startswith(">"):
            candidate = "\n".join(lines[:idx]).strip()
            if candidate:
                return candidate
            continue
        for marker in _REPLY_MARKERS:
            if trimmed.startswith(marker):
                candidate = "\n".join(lines[:idx]).strip()
                if candidate:
                    return candidate
                continue
        if any(pattern.match(trimmed) for pattern in _REPLY_HEADER_PATTERNS):
            candidate = "\n".join(lines[:idx]).strip()
            if candidate:
                return candidate
            continue
        if trimmed.startswith(_HEADER_BLOCK_PREFIXES):
            next_lines = [
                (lines[idx + offset].strip().lower() if idx + offset < len(lines) else "")
                for offset in (1, 2, 3)
            ]
            if any(next_line.startswith(_HEADER_BLOCK_PREFIXES) for next_line in next_lines if next_line):
                candidate = "\n".join(lines[:idx]).strip()
                if candidate:
                    return candidate
                continue
    return body.strip()


def extract_field_from_ics(ics_content: str, field_name: str) -> Optional[str]:
    """Extract, unfold (RFC 5545 Section 3.1), and unescape (Section 3.3.11) an ICS property.

    Args:
        ics_content: Raw iCalendar payload string.
        field_name: Target property name (e.g., ``SUMMARY``, ``DTSTART``, ``LOCATION``).

    Returns:
        Unescaped property value or ``None`` if absent.
    """
    if not ics_content or not field_name:
        return None
    pattern = rf"(?:^|\r?\n){re.escape(field_name)}(?:;[^:\r\n]*)?:([^\r\n]*(?:\r?\n[\t ][^\r\n]*)*)"
    match = re.search(pattern, ics_content, re.IGNORECASE)
    if not match:
        return None
    val = re.sub(r"\r?\n[\t ]", "", match.group(1))
    return (
        val.replace(r"\,", ",")
        .replace(r"\;", ";")
        .replace("\\\\", "\\")
        .replace(r"\n", "\n")
        .replace(r"\N", "\n")
        .strip()
        or None
    )


def extract_summary_from_ics(ics_content: str) -> Optional[str]:
    """Extract and unescape the ``SUMMARY`` property from iCalendar content."""
    return extract_field_from_ics(ics_content, "SUMMARY")


def sanitize_header_id(value: Optional[str]) -> str:
    """Strip CR, LF, and NUL control characters from a Message-ID header value."""
    if not value:
        return ""
    return re.sub(r"[\r\n\x00]+", "", str(value)).strip()


def resolve_invite_subject(
    original_email: Dict[str, Any],
    ics_content: str,
    *,
    event_data: Optional[Dict[str, Any]] = None,
    event_title: Optional[str] = None,
) -> str:
    """Resolve the invite headline/subject string following RFC 6047 precedence.

    Args:
        original_email: Original email metadata dict.
        ics_content: Rendered iCalendar invite string.
        event_data: Optional selected event candidate dictionary.
        event_title: Optional explicit override event title.

    Returns:
        Formatted invite subject string.
    """
    resolved_title = (
        (event_title or "").strip()
        or ((event_data.get("title") if event_data else "") or "").strip()
        or (original_email.get("event_title") or "").strip()
    )
    if not resolved_title and ics_content:
        resolved_title = extract_summary_from_ics(ics_content) or ""
    return format_invite_subject(resolved_title or None, original_email.get("subject"))


def filter_allowed_recipients(
    recipients: List[str],
    *,
    self_email: str = "",
    enforce_internal_domain: Optional[bool] = None,
    internal_domain: Optional[str] = None,
) -> List[str]:
    """Validate recipient email addresses and enforce optional internal-domain policy.

    Args:
        recipients: Candidate recipient strings or RFC 5322 display addresses.
        self_email: Bot mailbox address (always permitted even when internal policy is enabled).
        enforce_internal_domain: Explicit override for ``config.ENFORCE_INTERNAL_RECIPIENT_DOMAIN``.
        internal_domain: Explicit override for ``config.INTERNAL_EMAIL_DOMAIN``.

    Returns:
        Deduplicated list of syntactically valid, policy-permitted email addresses.
    """
    if not recipients:
        return []
    enforce_domain = (
        bool(getattr(config, "ENFORCE_INTERNAL_RECIPIENT_DOMAIN", False))
        if enforce_internal_domain is None
        else bool(enforce_internal_domain)
    )
    configured_domain = (
        getattr(config, "INTERNAL_EMAIL_DOMAIN", "")
        if internal_domain is None
        else internal_domain
    )
    normalized_domain = (configured_domain or "").strip().lower()
    self_addr = (self_email or "").strip().lower()

    allowed: List[str] = []
    for addr in recipients:
        if not isinstance(addr, str) or any(ch in addr for ch in ("\r", "\n", "\x00")):
            continue
        parsed = parseaddr(addr)[1].strip()
        if not parsed or not _EMAIL_ADDRESS_RE.fullmatch(parsed):
            continue
        if enforce_domain and normalized_domain and normalized_domain != "example.com":
            lowered = parsed.lower()
            if lowered != self_addr:
                dom = lowered.split("@", 1)[1]
                if dom != normalized_domain and not dom.endswith(f".{normalized_domain}"):
                    logger.warning(
                        "Blocked external recipient %s due to ENFORCE_INTERNAL_RECIPIENT_DOMAIN",
                        parsed,
                    )
                    continue
        if parsed not in allowed:
            allowed.append(parsed)
    return allowed


def build_calendar_mime_message(
    *,
    sender: str,
    recipients: List[str],
    subject: str,
    reply_message: str,
    ics_content: str,
    in_reply_to: Optional[str] = None,
) -> MIMEMultipart:
    """Build a ``multipart/mixed`` email containing plain, HTML, inline calendar, and `.ics`.

    Args:
        sender: Envelope/header From address.
        recipients: Sanitized list of destination email addresses.
        subject: Formatted subject line.
        reply_message: Plain-text human-readable invite message.
        ics_content: RFC 5545 calendar payload.
        in_reply_to: Optional parent ``Message-ID`` for thread headers.

    Returns:
        Populated ``MIMEMultipart`` message ready for SMTP or EWS ``mime_content``.
    """
    msg = MIMEMultipart("mixed")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    clean_mid = sanitize_header_id(in_reply_to)
    if clean_mid:
        msg["In-Reply-To"] = clean_mid
        msg["References"] = clean_mid

    alt = MIMEMultipart("alternative")
    msg.attach(alt)

    alt.attach(MIMEText(reply_message, "plain", "utf-8"))
    alt.attach(MIMEText(build_reply_message_html_from_text(reply_message), "html", "utf-8"))

    calendar_part = MIMEText(ics_content, "calendar", "utf-8")
    calendar_part.replace_header("Content-Type", 'text/calendar; method=REQUEST; charset="UTF-8"')
    calendar_part.add_header("Content-Disposition", "inline")
    calendar_part.add_header("Content-Class", "urn:content-classes:calendarmessage")
    alt.attach(calendar_part)

    ics_attachment = MIMEBase("text", "calendar", method="REQUEST", name="invite.ics")
    ics_attachment.set_payload(ics_content)
    encoders.encode_base64(ics_attachment)
    ics_attachment.add_header("Content-Disposition", "attachment", filename="invite.ics")
    msg.attach(ics_attachment)

    return msg


def save_outbound_mime_dump(mime_bytes: bytes, *, prefix: str = "outbound_invite") -> Optional[Path]:
    """Persist an outbound `.eml` debug dump when ``config.ENABLE_DEBUG_DUMPS`` is enabled."""
    if not getattr(config, "ENABLE_DEBUG_DUMPS", False):
        return None
    try:
        dump_dir = Path(getattr(config, "RESULTS_DB_PATH", ".")).parent
        dump_dir.mkdir(parents=True, exist_ok=True)
        dump_path = dump_dir / f"{prefix}_{utc_stamp('%Y%m%d_%H%M%S%f')}.eml"
        dump_path.write_bytes(mime_bytes)
        try:
            os.chmod(dump_path, 0o600)
        except OSError:
            pass
        logger.info("Saved outbound MIME to %s", dump_path)
        return dump_path
    except Exception as exc:
        logger.warning("Failed to save outbound MIME: %s", exc)
        return None

