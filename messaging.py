"""Helpers for composing user-facing reply messages."""

from typing import Iterable, List, Optional

import config


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
    at the end by default.

    Args:
        event_title: Candidate title or short event description extracted from the email.
        original_subject: Subject line of the original incoming email message (fallback).
        prefix: Optional prefix string (defaults to config.INVITE_SUBJECT_PREFIX).
        suffix: Optional suffix string (defaults to config.INVITE_SUBJECT_SUFFIX).

    Returns:
        Formatted subject line suitable for MIME Subject headers and calendar headlines.
    """
    pfx = getattr(config, 'INVITE_SUBJECT_PREFIX', '') if prefix is None else prefix
    sfx = getattr(config, 'INVITE_SUBJECT_SUFFIX', ' (iCal-Bot)') if suffix is None else suffix

    # Choose base title: event_title if present, else original_subject, else fallback
    base = (event_title or '').strip()
    if not base:
        base = (original_subject or '').strip()
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
