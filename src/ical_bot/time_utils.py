"""Shared timezone, timestamp, and ISO datetime parsing utilities for iCal Bot."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, tzinfo
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import config

logger = logging.getLogger(__name__)

DEFAULT_FALLBACK_TIMEZONE = "Europe/Berlin"

_DMY_DATETIME_RE = re.compile(
    r"^(?P<day>\d{1,2})[\.\-/](?P<month>\d{1,2})[\.\-/](?P<year>\d{2,4})"
    r"(?:[\sT]+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?(?::(?P<second>\d{2}))?)?$"
)


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return the current UTC timestamp formatted as an ISO-8601 string."""
    return utc_now().isoformat()


def utc_stamp(fmt: str = "%Y%m%d_%H%M%S%f") -> str:
    """Format the current UTC timestamp with ``strftime``.

    Args:
        fmt: ``strftime`` format specification.

    Returns:
        Formatted UTC timestamp string.
    """
    return utc_now().strftime(fmt)


def normalize_timezone_name(timezone_name: Optional[str] = None) -> str:
    """Resolve a configured IANA timezone name, defaulting `'UTC'`/blank to `Europe/Berlin`.

    Args:
        timezone_name: Explicit timezone name, or ``None`` to read ``config.DEFAULT_TIMEZONE``.

    Returns:
        Normalized IANA timezone name string.
    """
    candidate = (
        timezone_name
        if timezone_name is not None
        else (getattr(config, "DEFAULT_TIMEZONE", "") or "")
    ).strip()
    if not candidate or candidate.upper() == "UTC":
        return DEFAULT_FALLBACK_TIMEZONE
    return candidate


def resolve_timezone(timezone_name: Optional[str] = None) -> tzinfo:
    """Resolve an IANA timezone name to a ``tzinfo`` object with UTC fallback.

    Args:
        timezone_name: Timezone name to resolve; uses ``config.DEFAULT_TIMEZONE`` if omitted.

    Returns:
        Resolved ``ZoneInfo`` instance, or ``timezone.utc`` if resolution fails.
    """
    target = normalize_timezone_name(timezone_name)
    try:
        return ZoneInfo(target)
    except Exception:
        logger.warning("Unknown timezone '%s', falling back to UTC", target)
        return timezone.utc


def get_default_timezone(timezone_name: Optional[str] = None) -> tzinfo:
    """Return the configured default ``tzinfo`` object.

    Args:
        timezone_name: Optional override timezone name.

    Returns:
        Configured ``ZoneInfo`` or ``timezone.utc``.
    """
    return resolve_timezone(timezone_name)


def parse_iso_datetime(
    value: Optional[str],
    *,
    default_tz: Optional[tzinfo] = None,
    as_utc: bool = False,
) -> Optional[datetime]:
    """Parse an ISO-8601 or compact iCalendar timestamp string.

    Args:
        value: Input timestamp string.
        default_tz: Optional ``tzinfo`` to attach when the parsed datetime is naive.
        as_utc: When True, convert the returned datetime to ``timezone.utc`` (attaching
            ``default_tz`` or ``timezone.utc`` first if naive).

    Returns:
        Parsed ``datetime`` object, or ``None`` if input is empty or unparseable.
    """
    if not value:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    normalized = cleaned.replace("Z", "+00:00")
    if "T" not in normalized and " " in normalized:
        normalized = normalized.replace(" ", "T", 1)

    dt: Optional[datetime] = None
    try:
        if len(normalized) == 16 and normalized[8] == "T" and normalized.endswith("+00:00"):
            dt = datetime.strptime(normalized, "%Y%m%dT%H%M%S+00:00").replace(tzinfo=timezone.utc)
        elif len(normalized) == 15 and normalized[8] == "T":
            dt = datetime.strptime(normalized, "%Y%m%dT%H%M%S")
        else:
            dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if dt.tzinfo is None and default_tz is not None:
        dt = dt.replace(tzinfo=default_tz)
    if as_utc:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=default_tz or timezone.utc)
        return dt.astimezone(timezone.utc)
    return dt


def parse_flexible_datetime(
    value: Any,
    *,
    default_tz: Optional[tzinfo] = None,
    allow_rfc2822: bool = False,
) -> Optional[datetime]:
    """Parse ``datetime``, ISO-8601, German `'Uhr'`, European ``DD.MM.YYYY``, or RFC 2822 strings.

    Args:
        value: Input value (``datetime`` or string).
        default_tz: Fallback ``tzinfo`` attached when parsed result is naive.
        allow_rfc2822: Whether to attempt ``email.utils.parsedate_to_datetime`` as fallback.

    Returns:
        Timezone-aware (when ``default_tz`` provided) or parsed ``datetime``, or ``None``.
    """
    dt: Optional[datetime] = None
    if isinstance(value, datetime):
        dt = value
    else:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None

        cleaned = re.sub(r"\s+", " ", text.replace("Uhr", "").replace("uhr", "").strip())
        if cleaned.upper().endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"

        dt = parse_iso_datetime(cleaned, default_tz=None)
        if dt is None:
            iso_candidate = cleaned
            if "T" not in iso_candidate and re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}", iso_candidate):
                iso_candidate = iso_candidate.replace(" ", "T", 1)
            dt = parse_iso_datetime(iso_candidate, default_tz=None)

        if dt is None:
            match = _DMY_DATETIME_RE.match(cleaned)
            if match:
                year = int(match.group("year"))
                if year < 100:
                    year += 2000
                month = int(match.group("month"))
                day = int(match.group("day"))
                hour = int(match.group("hour") or 0)
                minute = int(match.group("minute") or 0)
                second = int(match.group("second") or 0)
                try:
                    dt = datetime(year, month, day, hour, minute, second)
                except ValueError:
                    return None

        if dt is None and allow_rfc2822:
            try:
                dt = parsedate_to_datetime(text)
            except (TypeError, ValueError):
                dt = None

        if dt is None:
            return None

    if dt.tzinfo is None and default_tz is not None:
        dt = dt.replace(tzinfo=default_tz)
    return dt


__all__ = [
    "DEFAULT_FALLBACK_TIMEZONE",
    "get_default_timezone",
    "normalize_timezone_name",
    "parse_flexible_datetime",
    "parse_iso_datetime",
    "resolve_timezone",
    "utc_now",
    "utc_now_iso",
    "utc_stamp",
]

