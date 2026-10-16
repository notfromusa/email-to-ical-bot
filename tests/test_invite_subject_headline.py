"""Unit tests for calendar headline and email subject formatting.

Verifies that when an email contains single or multiple events, the invite
email and calendar headline use the specific event candidate's short description / title
with the trailing identifier '(iCal-Bot)' (per RFC 6047 Section 2.3), rather than
inheriting the generic parent message subject.
"""

from unittest.mock import MagicMock, patch
import pytest

import config
from email_client import EmailClient
from exchange_client import ExchangeClient
from messaging import format_invite_subject
from web_ui import _send_calendar_invite_from_record


def test_format_invite_subject_standard():
    """Verify standard subject formatting places event title first with trailing (iCal-Bot)."""
    result = format_invite_subject(
        event_title="Thesis Defense",
        original_subject="Department Announcements for Next Week",
    )
    assert result == "Thesis Defense (iCal-Bot)"


def test_format_invite_subject_avoids_duplicate_suffix():
    """Verify that if event title already ends with (iCal-Bot), suffix is not duplicated."""
    result = format_invite_subject(
        event_title="Thesis Defense (iCal-Bot)",
        original_subject="Announcements",
    )
    assert result == "Thesis Defense (iCal-Bot)"


def test_format_invite_subject_cleans_legacy_prefix():
    """Verify that legacy 'iCal-Bot: ' prefix is stripped and placed at the end."""
    result = format_invite_subject(
        event_title="iCal-Bot: Thesis Defense",
        original_subject="Announcements",
    )
    assert result == "Thesis Defense (iCal-Bot)"


def test_format_invite_subject_fallback_to_original_subject():
    """Verify fallback to original email subject if event title is missing."""
    result = format_invite_subject(
        event_title="",
        original_subject="Team Sync",
    )
    assert result == "Team Sync (iCal-Bot)"


def test_format_invite_subject_fallback_to_default():
    """Verify fallback to 'Calendar Invite (iCal-Bot)' if both title and subject are missing."""
    result = format_invite_subject(
        event_title=None,
        original_subject=None,
    )
    assert result == "Calendar Invite (iCal-Bot)"


def test_format_invite_subject_custom_config_override():
    """Verify that prefix and suffix configuration overrides are respected."""
    with patch("config.INVITE_SUBJECT_PREFIX", "[Event] "), \
         patch("config.INVITE_SUBJECT_SUFFIX", ""):
        result = format_invite_subject("Thesis Defense", "Subject")
        assert result == "[Event] Thesis Defense"


def test_email_client_multi_event_subject_headlines():
    """Verify EmailClient produces distinct event-title headlines for multiple events in one email."""
    client = EmailClient()
    client.email_address = "bot@example.com"

    parent_email = {
        "id": "email-multi-1",
        "subject": "Weekly Schedule & Announcements",
        "sender": "organizer@example.com",
        "recipients": ["attendee@example.com"],
        "message_id": "<msg-1@example.com>",
    }

    event_candidate_1 = {
        "title": "Lab Meeting",
        "start_datetime": "2026-10-20T10:00:00Z",
        "end_datetime": "2026-10-20T11:00:00Z",
    }
    event_candidate_2 = {
        "title": "PhD Defense: Jane Doe",
        "start_datetime": "2026-10-21T14:00:00Z",
        "end_datetime": "2026-10-21T16:00:00Z",
    }

    ics_content_1 = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Lab Meeting\r\nEND:VEVENT\r\nEND:VCALENDAR"
    ics_content_2 = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:PhD Defense: Jane Doe\r\nEND:VEVENT\r\nEND:VCALENDAR"

    # Send Candidate 1 in dry run
    res1 = client.send_reply_with_calendar(
        parent_email,
        ics_content_1,
        dry_run=True,
        event_data=event_candidate_1,
    )
    assert res1["subject"] == "Lab Meeting (iCal-Bot)"

    # Send Candidate 2 in dry run
    res2 = client.send_reply_with_calendar(
        parent_email,
        ics_content_2,
        dry_run=True,
        event_data=event_candidate_2,
    )
    assert res2["subject"] == "PhD Defense: Jane Doe (iCal-Bot)"


def test_exchange_client_multi_event_subject_headlines():
    """Verify ExchangeClient produces distinct event-title headlines for multiple events."""
    client = ExchangeClient.__new__(ExchangeClient)
    client.email_address = "bot@example.com"
    client._filter_allowed_recipients = lambda recs: recs

    parent_email = {
        "id": "email-multi-ews",
        "subject": "Fwd: Multiple Seminars",
        "sender": "prof@example.com",
        "recipients": ["student@example.com"],
        "message_id": "<ews-msg-1@example.com>",
    }

    event_1 = {"title": "AI Alignment Seminar"}
    event_2 = {"title": "Robotics Colloquium"}

    ics_1 = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:AI Alignment Seminar\r\nEND:VEVENT\r\nEND:VCALENDAR"
    ics_2 = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Robotics Colloquium\r\nEND:VEVENT\r\nEND:VCALENDAR"

    res1 = client.send_reply_with_calendar(
        parent_email,
        ics_1,
        dry_run=True,
        event_data=event_1,
    )
    assert res1["subject"] == "AI Alignment Seminar (iCal-Bot)"

    res2 = client.send_reply_with_calendar(
        parent_email,
        ics_2,
        dry_run=True,
        event_data=event_2,
    )
    assert res2["subject"] == "Robotics Colloquium (iCal-Bot)"


def test_email_client_extracts_summary_fallback_from_ics():
    """Verify EmailClient extracts SUMMARY from ICS when neither event_data nor event_title is passed."""
    client = EmailClient()
    client.email_address = "bot@example.com"

    parent_email = {
        "id": "email-fallback",
        "subject": "Newsletter",
        "sender": "sender@example.com",
        "recipients": ["user@example.com"],
    }
    ics = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "BEGIN:VEVENT\r\n"
        "SUMMARY:Keynote Lecture\\, Hall A\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR"
    )

    res = client.send_reply_with_calendar(
        parent_email,
        ics,
        dry_run=True,
    )
    assert res["subject"] == "Keynote Lecture, Hall A (iCal-Bot)"


def test_web_ui_send_calendar_invite_multi_event_selection():
    """Verify that _send_calendar_invite_from_record passes the selected event candidate's title."""
    record = {
        "email_id": "multi-event-rec-1",
        "email_subject": "General Department Newsletter",
        "email_sender": "dept@example.com",
        "email_recipients": '["attendee@example.com"]',
        "email_message_id": "<rec-msg-1@example.com>",
        "event_candidates": [
            {
                "title": "Welcome Breakfast",
                "start_datetime": "2026-10-25T09:00:00Z",
                "end_datetime": "2026-10-25T10:00:00Z",
                "location": "Lounge",
                "description": "Coffee and pastries",
            },
            {
                "title": "Invited Talk by Alice",
                "start_datetime": "2026-10-25T14:00:00Z",
                "end_datetime": "2026-10-25T15:00:00Z",
                "location": "Auditorium",
                "description": "Keynote presentation",
            },
        ],
        "selected_event_index": 0,
        "ics_content": "BEGIN:VCALENDAR\r\nEND:VCALENDAR",
    }

    mock_mail_client = MagicMock()
    mock_mail_client.send_reply_with_calendar.return_value = {
        "status": "dry_run_preview",
        "recipients": ["attendee@example.com"],
        "subject": "Invited Talk by Alice (iCal-Bot)",
    }

    with patch("web_ui.EmailClient", return_value=mock_mail_client), \
         patch("config.EMAIL_PROTOCOL", "IMAP"), \
         patch("config.DRY_RUN", True):
        # Select Candidate 1 ("Invited Talk by Alice")
        send_result, event_payload, resolved_index, ics = _send_calendar_invite_from_record(
            record,
            selected_event_index=1,
        )

        assert resolved_index == 1
        assert event_payload["title"] == "Invited Talk by Alice"
        assert mock_mail_client.send_reply_with_calendar.called
        call_args = mock_mail_client.send_reply_with_calendar.call_args[0]
        call_kwargs = mock_mail_client.send_reply_with_calendar.call_args[1]
        assert call_kwargs["event_title"] == "Invited Talk by Alice"
        assert call_kwargs["event_data"]["title"] == "Invited Talk by Alice"
        assert call_args[0]["event_title"] == "Invited Talk by Alice"
