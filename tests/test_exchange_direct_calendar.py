"""Unit tests for Exchange direct calendar creation (Option A: native Outlook editability).

Validates:
- RFC 5545 field extraction and unescaping
- EWSDateTime parsing and timezone localization
- Direct CalendarItem creation in user's calendar folder
- Updating existing CalendarItem upon event re-send/update
- Handling of attendee invitation options (SEND_TO_NONE vs SEND_TO_ALL_AND_SAVE_COPY)
- Storage of calendar_item_id in database
- Stable UID and SEQUENCE in calendar generator
"""

from datetime import datetime
from unittest.mock import MagicMock, patch
import pytest
from exchangelib import EWSTimeZone, EWSDateTime, Account
from exchangelib.folders import Calendar
from exchangelib.items import SEND_TO_ALL_AND_SAVE_COPY, SEND_TO_NONE

import config
from exchange_client import ExchangeClient
from calendar_generator import CalendarGenerator
from storage import StorageManager


def test_extract_field_from_ics_unfolding_and_unescaping():
    """Verify unfolding of RFC 5545 multi-line properties and character unescaping."""
    ics_sample = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:meeting-12345-uid\r\n"
        "DTSTART:20261025T083000Z\r\n"
        "DTEND:20261025T100000Z\r\n"
        "SUMMARY:PhD Defense: Long\\, Complex\\; Title\r\n"
        "LOCATION:Room 2.102\\, Keplerstr. 17\r\n"
        "DESCRIPTION:First line of description\\nSecond line with folded\r\n"
        "  continuation and escaped \\\\ backslash\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR"
    )

    summary = ExchangeClient._extract_field_from_ics(ics_sample, "SUMMARY")
    assert summary == "PhD Defense: Long, Complex; Title"

    uid = ExchangeClient._extract_field_from_ics(ics_sample, "UID")
    assert uid == "meeting-12345-uid"

    dtstart = ExchangeClient._extract_field_from_ics(ics_sample, "DTSTART")
    assert dtstart == "20261025T083000Z"

    dtend = ExchangeClient._extract_field_from_ics(ics_sample, "DTEND")
    assert dtend == "20261025T100000Z"

    location = ExchangeClient._extract_field_from_ics(ics_sample, "LOCATION")
    assert location == "Room 2.102, Keplerstr. 17"

    description = ExchangeClient._extract_field_from_ics(ics_sample, "DESCRIPTION")
    assert "First line of description\nSecond line with folded continuation and escaped \\ backslash" in description


def test_to_ews_datetime_conversions():
    """Verify datetime parsing into timezone-aware EWSDateTime objects."""
    client = ExchangeClient.__new__(ExchangeClient)
    tz = EWSTimeZone("Europe/Berlin")

    # UTC string
    ews_utc = client._to_ews_datetime("2026-10-25T08:30:00Z", tz)
    assert isinstance(ews_utc, EWSDateTime)
    assert ews_utc.tzinfo == tz

    # Naive ISO string (localized to default timezone)
    ews_naive = client._to_ews_datetime("2026-10-25T10:30:00", tz)
    assert isinstance(ews_naive, EWSDateTime)
    assert ews_naive.hour == 10
    assert ews_naive.minute == 30

    # iCalendar basic format
    ews_basic = client._to_ews_datetime("20261025T083000Z", tz)
    assert isinstance(ews_basic, EWSDateTime)
    assert ews_basic.year == 2026
    assert ews_basic.month == 10
    assert ews_basic.day == 25


def test_exchange_direct_calendar_dry_run():
    """Verify that in dry run, ExchangeClient returns preview without saving."""
    client = ExchangeClient.__new__(ExchangeClient)
    client.email_address = "user@ifp.uni-stuttgart.de"
    client._filter_allowed_recipients = lambda recs: recs

    email_data = {
        "id": "email-dry-run",
        "subject": "Invitation: Department Colloquium",
        "sender": "speaker@uni-stuttgart.de",
        "recipients": ["user@ifp.uni-stuttgart.de"],
    }
    event_data = {
        "title": "Department Colloquium",
        "start_datetime": "2026-11-05T14:00:00Z",
        "end_datetime": "2026-11-05T15:30:00Z",
        "location": "V 47.01",
        "description": "Guest lecture on Photogrammetry.",
    }
    ics_dummy = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Department Colloquium\r\nEND:VEVENT\r\nEND:VCALENDAR"

    with patch.object(config, "EXCHANGE_DIRECT_CALENDAR_CREATION", True):
        result = client.send_reply_with_calendar(
            email_data,
            ics_dummy,
            dry_run=True,
            event_data=event_data,
        )

    assert result["status"] == "dry_run_preview"
    assert result["direct_calendar_item"] is True
    assert result["subject"] == "Department Colloquium (iCal-Bot)"
    assert result["recipients"] == ["user@ifp.uni-stuttgart.de"]


def test_exchange_direct_calendar_save_new():
    """Verify live direct calendar creation creates a CalendarItem in account.calendar."""
    client = ExchangeClient.__new__(ExchangeClient)
    client.email_address = "user@ifp.uni-stuttgart.de"
    client._filter_allowed_recipients = lambda recs: recs

    mock_account = MagicMock(spec=Account)
    mock_calendar_folder = MagicMock(spec=Calendar)
    mock_calendar_folder.account = mock_account
    mock_account.calendar = mock_calendar_folder
    client.account = mock_account

    email_data = {
        "id": "email-live-1",
        "subject": "Lab Meeting",
        "sender": "boss@ifp.uni-stuttgart.de",
        "recipients": ["user@ifp.uni-stuttgart.de"],
    }
    event_data = {
        "title": "Lab Meeting",
        "start_datetime": "2026-11-10T09:00:00Z",
        "end_datetime": "2026-11-10T10:00:00Z",
        "location": "Room 101",
        "description": "Weekly status update",
    }
    ics_dummy = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Lab Meeting\r\nEND:VEVENT\r\nEND:VCALENDAR"

    saved_items = []

    def mock_save(self_item, *args, **kwargs):
        self_item.id = "EWS_ITEM_ID_999"
        saved_items.append((self_item, kwargs))

    with patch("exchange_client.CalendarItem.save", mock_save):
        with patch.object(config, "EXCHANGE_DIRECT_CALENDAR_CREATION", True):
            with patch.object(config, "EXCHANGE_CALENDAR_INVITE_ATTENDEES", False):
                result = client.send_reply_with_calendar(
                    email_data,
                    ics_dummy,
                    dry_run=False,
                    event_data=event_data,
                )

    assert result["status"] == "sent"
    assert result["calendar_item_id"] == "EWS_ITEM_ID_999"
    assert result["direct_calendar_item"] is True
    assert len(saved_items) == 1

    item, kwargs = saved_items[0]
    assert item.subject == "Lab Meeting (iCal-Bot)"
    assert item.location == "Room 101"
    assert item.categories == ["iCal-Bot"]
    assert kwargs.get("send_meeting_invitations") == SEND_TO_NONE


def test_exchange_direct_calendar_invite_attendees():
    """Verify that when invite_attendees is enabled, attendees receive invitations."""
    client = ExchangeClient.__new__(ExchangeClient)
    client.email_address = "user@ifp.uni-stuttgart.de"
    client._filter_allowed_recipients = lambda recs: recs

    mock_account = MagicMock(spec=Account)
    mock_calendar_folder = MagicMock(spec=Calendar)
    mock_calendar_folder.account = mock_account
    mock_account.calendar = mock_calendar_folder
    client.account = mock_account

    email_data = {
        "id": "email-live-2",
        "subject": "Joint Seminar",
        "sender": "speaker@uni-stuttgart.de",
        "recipients": ["user@ifp.uni-stuttgart.de", "external@colleague.org"],
    }
    event_data = {
        "title": "Joint Seminar",
        "start_datetime": "2026-11-12T13:00:00Z",
        "end_datetime": "2026-11-12T14:00:00Z",
        "location": "Auditorium",
        "description": "Joint Seminar Discussion",
    }
    ics_dummy = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Joint Seminar\r\nEND:VEVENT\r\nEND:VCALENDAR"

    saved_items = []

    def mock_save(self_item, *args, **kwargs):
        self_item.id = "EWS_ITEM_ID_888"
        saved_items.append((self_item, kwargs))

    with patch("exchange_client.CalendarItem.save", mock_save):
        with patch.object(config, "EXCHANGE_DIRECT_CALENDAR_CREATION", True):
            with patch.object(config, "EXCHANGE_CALENDAR_INVITE_ATTENDEES", True):
                result = client.send_reply_with_calendar(
                    email_data,
                    ics_dummy,
                    dry_run=False,
                    event_data=event_data,
                )

    assert result["status"] == "sent"
    assert len(saved_items) == 1
    item, kwargs = saved_items[0]
    assert kwargs.get("send_meeting_invitations") == SEND_TO_ALL_AND_SAVE_COPY
    assert item.required_attendees == ["external@colleague.org"]


def test_exchange_direct_calendar_update_existing():
    """Verify that if calendar_item_id exists, the existing CalendarItem is updated."""
    client = ExchangeClient.__new__(ExchangeClient)
    client.email_address = "user@ifp.uni-stuttgart.de"
    client._filter_allowed_recipients = lambda recs: recs

    mock_account = MagicMock()
    mock_existing_item = MagicMock()
    mock_existing_item.id = "EWS_EXISTING_123"
    mock_account.calendar.get.return_value = mock_existing_item
    client.account = mock_account

    email_data = {
        "id": "email-update-1",
        "subject": "Lab Meeting - Rescheduled",
        "sender": "user@ifp.uni-stuttgart.de",
        "recipients": ["user@ifp.uni-stuttgart.de"],
        "calendar_item_id": "EWS_EXISTING_123",
    }
    event_data = {
        "title": "Lab Meeting (New Time)",
        "start_datetime": "2026-11-10T11:00:00Z",
        "end_datetime": "2026-11-10T12:00:00Z",
        "location": "Room 205",
        "description": "Moved one hour later",
    }
    ics_dummy = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Lab Meeting (New Time)\r\nEND:VEVENT\r\nEND:VCALENDAR"

    with patch.object(config, "EXCHANGE_DIRECT_CALENDAR_CREATION", True):
        result = client.send_reply_with_calendar(
            email_data,
            ics_dummy,
            dry_run=False,
            event_data=event_data,
        )

    mock_account.calendar.get.assert_called_once_with(id="EWS_EXISTING_123")
    assert mock_existing_item.subject == "Lab Meeting (New Time) (iCal-Bot)"
    assert mock_existing_item.location == "Room 205"
    assert mock_existing_item.save.called
    assert result["calendar_item_id"] == "EWS_EXISTING_123"


def test_storage_calendar_item_id_persistence(tmp_path):
    """Verify that storage stores and retrieves calendar_item_id."""
    db_path = tmp_path / "test_results.db"
    db = StorageManager(str(db_path))

    db.record_result(
        email_id="msg-cal-test-1",
        email_subject="Test Event",
        email_sender="sender@example.com",
        email_date="2026-10-19T10:00:00Z",
        has_event=True,
        event_data={"title": "Test Event", "start_datetime": "2026-10-20T10:00:00Z"},
        sent=False,
        calendar_item_id=None,
    )

    record = db.get_result("msg-cal-test-1")
    assert record is not None
    assert record.get("calendar_item_id") is None

    db.mark_event_sent(
        "msg-cal-test-1",
        status="invite_sent",
        sent=True,
        calendar_item_id="EWS_ITEM_ID_XYZ",
    )

    updated_record = db.get_result("msg-cal-test-1")
    assert updated_record["calendar_item_id"] == "EWS_ITEM_ID_XYZ"


def test_calendar_generator_stable_uid():
    """Verify that calendar generator uses supplied event_data uid and sequence."""
    generator = CalendarGenerator()
    event_data = {
        "title": "Annual Review",
        "start_datetime": "2026-12-01T10:00:00Z",
        "end_datetime": "2026-12-01T11:00:00Z",
        "uid": "custom-stable-uid-42",
        "sequence": 3,
    }

    ics = generator.create_ics_invite(event_data, recipients=["user@example.com"])
    assert "UID:custom-stable-uid-42" in ics
    assert "SEQUENCE:3" in ics


def test_exchange_fallback_to_mime_when_direct_calendar_disabled():
    """Verify that when EXCHANGE_DIRECT_CALENDAR_CREATION is False, fallback to MIME email occurs."""
    client = ExchangeClient.__new__(ExchangeClient)
    client.email_address = "user@ifp.uni-stuttgart.de"
    client._filter_allowed_recipients = lambda recs: recs

    mock_account = MagicMock(spec=Account)
    mock_calendar_folder = MagicMock(spec=Calendar)
    mock_calendar_folder.account = mock_account
    mock_account.calendar = mock_calendar_folder
    client.account = mock_account

    email_data = {
        "id": "email-mime-fallback",
        "subject": "Colloquium",
        "sender": "speaker@uni-stuttgart.de",
        "recipients": ["user@ifp.uni-stuttgart.de"],
    }
    event_data = {
        "title": "Colloquium",
        "start_datetime": "2026-11-20T14:00:00Z",
        "end_datetime": "2026-11-20T15:00:00Z",
    }
    ics_dummy = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Colloquium\r\nEND:VEVENT\r\nEND:VCALENDAR"

    with patch.object(config, "EXCHANGE_DIRECT_CALENDAR_CREATION", False):
        result = client.send_reply_with_calendar(
            email_data,
            ics_dummy,
            dry_run=True,
            event_data=event_data,
        )

    assert result["status"] == "dry_run_preview"
    assert result.get("direct_calendar_item") is not True
    assert result["subject"] == "Colloquium (iCal-Bot)"
