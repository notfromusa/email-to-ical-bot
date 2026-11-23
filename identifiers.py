"""Helpers for generating stable unique identifiers for emails."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Union

from ical_bot.time_utils import parse_iso_datetime

_FIELD_SEPARATOR = "\x1f"

# Match common forward prefixes (e.g. English Fwd/FW, German WG, French TR, Spanish VS/RV, Italian I)
# per RFC 5322 section 3.6.5 subject conventions and common MUA localizations.
FORWARD_SUBJECT_PREFIX_REGEX = re.compile(
    r'^(?:\[[^\]]*\]\s*)*(?:fwd?|fw|wg|vs|tr|rv|fs)\b[\s:]',
    re.IGNORECASE,
)
REPLY_SUBJECT_PREFIX_REGEX = re.compile(
    r'^(?:\[[^\]]*\]\s*)*(?:re|aw|antw|antwort)\b[\s:]',
    re.IGNORECASE,
)
FORWARD_BODY_PATTERNS = [
    re.compile(r'[-=]{3,}\s*(?:forwarded message|weitergeleitete nachricht)\s*[-=]{3,}', re.IGNORECASE),
    re.compile(r'begin forwarded message:', re.IGNORECASE),
    re.compile(r'anfang der weitergeleiteten nachricht:', re.IGNORECASE),
]


def is_forwarded_email(subject: Optional[str] = None, body: Optional[str] = None) -> bool:
    """Determine whether an email is a forwarded message rather than a direct message or reply trail.

    Per RFC 5322 and standard MUA conventions (Outlook, Thunderbird, Gmail, Apple Mail),
    forwarded emails carry subject prefixes like 'Fwd:' or German 'WG:', or embedded MIME / body
    delimiters like '---------- Forwarded message ---------'. In contrast to reply chains where
    quoted trails are typically redundant historical context, forwarded messages carry the
    primary information payload intended for calendar extraction.

    Args:
        subject: The email subject line.
        body: The plain text email body.

    Returns:
        bool: True if the email represents a forwarded message.
    """
    if subject:
        clean_subj = subject.strip()
        while clean_subj.startswith('['):
            idx = clean_subj.find(']')
            if idx != -1:
                clean_subj = clean_subj[idx + 1:].strip()
            else:
                break
        if FORWARD_SUBJECT_PREFIX_REGEX.match(clean_subj):
            return True
        if REPLY_SUBJECT_PREFIX_REGEX.match(clean_subj):
            if body and any(p.search(body) for p in FORWARD_BODY_PATTERNS):
                return True
            return False

    if body:
        if any(p.search(body) for p in FORWARD_BODY_PATTERNS):
            return True

    return False


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string via shared time utilities."""
    return parse_iso_datetime(value, default_tz=None)


def normalize_timestamp(value: Optional[Union[str, datetime]]) -> str:
    """Return an ISO-8601 UTC timestamp for the provided value if possible."""
    if value is None:
        return ""

    dt: Optional[datetime] = None

    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text:
            dt = _parse_iso_datetime(text)
            if dt is None:
                try:
                    dt = parsedate_to_datetime(text)
                except (TypeError, ValueError):
                    dt = None
        if dt is None:
            return text

    if dt.tzinfo is None:
        dt_utc = dt.replace(tzinfo=timezone.utc)
    else:
        try:
            dt_utc = dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            timestamp = dt.timestamp()
            dt_utc = datetime.fromtimestamp(timestamp, tz=timezone.utc)

    return dt_utc.isoformat()


def fingerprint_body(body: Optional[str]) -> str:
    """Create a lightweight hash of the email body for identifier entropy."""
    if not body:
        return ""
    digest = hashlib.sha256(body.encode('utf-8', errors='ignore')).hexdigest()
    return digest


def normalize_message_id(message_id: Optional[str]) -> str:
    """Normalize an RFC 5322 Message-ID header for case- and bracket-insensitive matching.

    Args:
        message_id: Raw ``Message-ID`` header value or ``None``.

    Returns:
        Normalized lowercase identifier without outer angle brackets or whitespace.
    """
    if not message_id:
        return ""
    cleaned = re.sub(r"[\r\n\x00]+", "", str(message_id)).strip()
    if cleaned.startswith("<") and cleaned.endswith(">") and len(cleaned) >= 2:
        cleaned = cleaned[1:-1].strip()
    return cleaned.lower()


def generate_email_id(
    *,
    message_id: Optional[str],
    timestamp: Optional[str],
    sender: Optional[str],
    subject: Optional[str],
    body_hash: Optional[str] = None,
) -> str:
    """Build a deterministic hashed identifier for an email."""
    components = [
        (message_id or "").strip(),
        (timestamp or ""),
        (sender or "").strip().lower(),
        (subject or "").strip(),
        (body_hash or ""),
    ]
    payload = _FIELD_SEPARATOR.join(components)
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    return f"msg_{digest}"


def generate_stable_email_id(
    *,
    message_id: Optional[str],
    timestamp: Optional[str],
    sender: Optional[str],
    subject: Optional[str],
    body_hash: Optional[str] = None,
) -> str:
    """Build a stable hashed email identifier immune to attachment-parsing toggles.

    When a non-empty RFC 5322 ``Message-ID`` is available, the identifier is
    derived strictly from header metadata (normalized ``Message-ID``, timestamp,
    sender, and subject) so toggling ``ATTACHMENT_PARSING_ENABLED`` or minor body
    re-decoding does not mutate the ID or re-trigger LLM analysis. When no
    ``Message-ID`` is present, falls back to :func:`generate_email_id` with
    ``body_hash`` entropy.

    Args:
        message_id: RFC 5322 ``Message-ID`` header if available.
        timestamp: Normalized ISO-8601 timestamp string.
        sender: Sender email address or header value.
        subject: Email subject string.
        body_hash: Fallback SHA-256 hex digest of the message body when ``message_id`` is absent.

    Returns:
        Deterministic identifier prefixed with ``msg_``.
    """
    norm_mid = normalize_message_id(message_id)
    if norm_mid:
        components = [
            norm_mid,
            (timestamp or "").strip(),
            (sender or "").strip().lower(),
            (subject or "").strip(),
        ]
        payload = _FIELD_SEPARATOR.join(components)
        digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
        return f"msg_{digest}"
    return generate_email_id(
        message_id=message_id,
        timestamp=timestamp,
        sender=sender,
        subject=subject,
        body_hash=body_hash,
    )


__all__ = [
    "generate_email_id",
    "generate_stable_email_id",
    "normalize_message_id",
    "fingerprint_body",
    "normalize_timestamp",
    "is_forwarded_email",
]
