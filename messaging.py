"""Helpers for composing user-facing reply messages."""

from typing import Iterable, List

import config


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
