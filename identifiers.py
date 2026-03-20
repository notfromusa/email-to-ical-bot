"""Helpers for generating stable unique identifiers for emails."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Union

_FIELD_SEPARATOR = "\x1f"


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None


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


__all__ = [
    "generate_email_id",
    "fingerprint_body",
    "normalize_timestamp",
]
