"""Helpers for composing user-facing reply messages."""

import re
from typing import Iterable, Optional

import config


def _sanitize_subject_part(value: Optional[str]) -> str:
    """Strip CR, LF, and NUL characters from MIME subject line fragments."""
    if not value:
        return ""
    cleaned = re.sub(r"[\r\n\x00]+", " ", str(value))
    return re.sub(r"\s+", " ", cleaned).strip()


def format_invite_subject(
    event_title: Optional[str] = None,
    original_subject: Optional[str] = None,
    *,
    prefix: Optional[str] = None,
    suffix: Optional[str] = None,
) -> str:
    """Format the outbound email/meeting subject line for calendar invites.

    In accordance with RFC 6047 (iMIP) Section 2.3, the MIME Subject header field
    serves as the primary human-readable title for calendar messages and is used
    by calendar clients (such as Microsoft Exchange, Outlook, and Google Calendar)
    to populate the appointment headline on the calendar grid.

    This function prioritizes the specific event's title/candidate description
    over the generic parent email subject. To avoid calendar clutter while preserving
    clear sender provenance, an identifier suffix (e.g., ' (iCal-Bot)') is placed
    at the end by default. Control characters (CR/LF/NUL) are stripped to prevent
    MIME header folding errors or injection.

    Args:
        event_title: Candidate title or short event description extracted from the email.
        original_subject: Subject line of the original incoming email message (fallback).
        prefix: Optional prefix string (defaults to config.INVITE_SUBJECT_PREFIX).
        suffix: Optional suffix string (defaults to config.INVITE_SUBJECT_SUFFIX).

    Returns:
        Formatted subject line suitable for MIME Subject headers and calendar headlines.
    """
    raw_pfx = getattr(config, 'INVITE_SUBJECT_PREFIX', '') if prefix is None else prefix
    raw_sfx = getattr(config, 'INVITE_SUBJECT_SUFFIX', ' (iCal-Bot)') if suffix is None else suffix
    pfx = re.sub(r"[\r\n\x00]+", " ", str(raw_pfx or ""))
    sfx = re.sub(r"[\r\n\x00]+", " ", str(raw_sfx or ""))

    # Choose base title: event_title if present, else original_subject, else fallback
    base = _sanitize_subject_part(event_title)
    if not base:
        base = _sanitize_subject_part(original_subject)
    if not base:
        base = 'Calendar Invite'

    # Strip legacy "iCal-Bot: " prefix from base if present to prevent duplication
    if base.lower().startswith('ical-bot:'):
        base = base[len('ical-bot:'):].strip()

    subject = base

    # Apply prefix if configured and not already present
    if pfx and not subject.lower().startswith(pfx.lower().strip()):
        subject = f"{pfx}{subject}"

    # Apply suffix if configured and not already present at the end
    sfx_stripped = sfx.strip()
    if sfx_stripped and not subject.rstrip().lower().endswith(sfx_stripped.lower()):
        subject = f"{subject.rstrip()}{sfx}"

    return subject.strip()


def build_reply_message(event_data: dict, recipients: Iterable[str]) -> str:
    """Compose the reply body shared with calendar invites."""
    title = event_data.get('title') or 'Untitled event'
    start = event_data.get('start_datetime') or '—'
    end = event_data.get('end_datetime') or '—'
    location = event_data.get('location') or ''
    description = (event_data.get('description') or '').strip()

    lines = [
        "Hello,",
        "",
        "I prepared a calendar invite based on your email. Please review it before accepting.",
        "",
        f"Event: {title}",
        f"Start: {start}",
        f"End: {end}",
    ]

    if location:
        lines.append(f"Location: {location}")

    if description:
        lines.extend(["", "Description:", description])

    lines.extend([
        "",
        "The calendar file is attached.",
        "",
        f"Disclaimer: This event was generated automatically by iCal Bot ({config.EMAIL_ADDRESS}).",
        "Please verify the details before accepting.",
        "",
        "— iCal Bot",
    ])

    return "\n".join(lines)


def build_reply_message_html(event_data: dict, recipients: Iterable[str]) -> str:
    """Compose an HTML reply body that preserves line breaks."""
    text = build_reply_message(event_data, recipients)
    return build_reply_message_html_from_text(text)


def build_reply_message_html_from_text(text: str) -> str:
    """Convert a plain-text reply message to HTML with line breaks preserved."""
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    html_body = "<br>".join(escaped.split("\n"))
    return f"<div style=\"font-family: Arial, sans-serif; white-space: normal;\">{html_body}</div>"


def build_calendar_description(event_data: dict) -> str:
    """Compose the structured body/description for calendar invites and Exchange items.

    Includes a concise digest of key meeting details (Event title, Start, End,
    Location), followed by the email description/agenda, and concludes with the
    automated provenance disclaimer.

    Args:
        event_data: Dictionary containing event attributes ('title', 'start_datetime',
            'end_datetime', 'location', 'description').

    Returns:
        Formatted multi-line description string for calendar events.
    """
    title = (event_data.get('title') or event_data.get('summary') or '').strip()
    start = (event_data.get('start_datetime') or event_data.get('start') or '').strip()
    end = (event_data.get('end_datetime') or event_data.get('end') or '').strip()
    location = (event_data.get('location') or '').strip()
    description = (event_data.get('description') or event_data.get('details') or '').strip()
    bot_email = getattr(config, 'EMAIL_ADDRESS', '')

    # If the description already contains our formatted digest and disclaimer, avoid duplicate wrapping
    if description.startswith("Event: ") and "Description:" in description and "generated automatically by ical bot" in description.lower():
        return description

    lines = []
    if title:
        lines.append(f"Event: {title}")
    if start:
        lines.append(f"Start: {start}")
    if end:
        lines.append(f"End: {end}")
    if location:
        lines.append(f"Location: {location}")

    if description:
        if lines:
            lines.append("")
        lines.append("Description:")
        lines.append(description)

    disclaimer_email = f" ({bot_email})" if bot_email else ""
    disclaimer = (
        f"Disclaimer: This event was generated automatically by iCal Bot{disclaimer_email}.\n"
        "Please verify the details before accepting."
    )

    if "generated automatically by ical bot" not in description.lower():
        if lines:
            lines.append("")
        lines.append(disclaimer)

    return "\n".join(lines)

