"""Mail adapter seam and shared MIME/recipient/reply-trail utilities."""

from ical_bot.mail.common import (
    build_calendar_mime_message,
    extract_field_from_ics,
    extract_summary_from_ics,
    filter_allowed_recipients,
    resolve_invite_subject,
    sanitize_header_id,
    save_outbound_mime_dump,
    strip_reply_trail,
)
from ical_bot.mail.protocol import MailAdapter, create_mail_adapter

__all__ = [
    "MailAdapter",
    "build_calendar_mime_message",
    "create_mail_adapter",
    "extract_field_from_ics",
    "extract_summary_from_ics",
    "filter_allowed_recipients",
    "resolve_invite_subject",
    "sanitize_header_id",
    "save_outbound_mime_dump",
    "strip_reply_trail",
]

