"""Tests for forwarded email detection, reply trail preservation, and rescan behavior.

Covers:
- Forwarded message recognition across English and German conventions (RFC 5322, DIN 5008).
- Preservation of forwarded message content during reply trail trimming.
- Rescan prompting to guide LLMs towards diligent event discovery.
- Resolution of double-check QA loop false-positive unparseable JSON errors when no event is detected.
- Web UI rescan endpoint behavior.
"""

import json
from unittest.mock import MagicMock, patch
import pytest

from email_client import EmailClient
from exchange_client import ExchangeClient
from identifiers import is_forwarded_email
from llm_client import LLMClient, LLMError
from web_ui import app, storage


class TestForwardedEmailDetection:
    """Validate identification of forwarded messages vs regular emails and replies."""

    def test_english_forward_subject(self):
        """Subject with Fwd: prefix should be recognized as forwarded."""
        subj = 'Fwd: [An alle Studierenden:] Info-Vortrag "Promovieren" I Doing a doctorate'
        assert is_forwarded_email(subject=subj) is True

    def test_german_forward_subject(self):
        """Subject with German WG: (Weitergeleitet) prefix should be recognized."""
        subj = 'WG: Info-Vortrag "Promovieren"'
        assert is_forwarded_email(subject=subj) is True

    def test_bracketed_prefix_forward(self):
        """Subject with mailing list tag before Fwd: should be recognized."""
        subj = '[Staff-List] Fwd: Faculty Meeting on Friday'
        assert is_forwarded_email(subject=subj) is True

    def test_forward_body_banners(self):
        """Body with standard MUA forward banners should be recognized even without Fwd in subject."""
        banners = [
            '---------- Forwarded message ---------',
            '---------- Weitergeleitete Nachricht ---------',
            '----- Forwarded Message -----',
            '----- Weitergeleitete Nachricht -----',
            'Begin forwarded message:',
            'Anfang der weitergeleiteten Nachricht:',
        ]
        for banner in banners:
            body = f"Please see this note:\n{banner}\nFrom: sender@example.com\nEvent details here..."
            assert is_forwarded_email(subject="Important Announcement", body=body) is True

    def test_normal_email_and_replies_not_forward(self):
        """Direct emails and replies should not be treated as forwards."""
        assert is_forwarded_email(subject="Meeting tomorrow", body="Let's meet at 2pm.") is False
        assert is_forwarded_email(subject="Re: Meeting tomorrow", body="Sounds good!") is False
        assert is_forwarded_email(subject="AW: Termin morgen", body="Passt mir.") is False

    def test_reply_to_forward_without_new_forward_body(self):
        """Subject starting with Re: to a forward should be treated as a reply, not a forward."""
        subj = "Re: Fwd: Meeting tomorrow"
        body = "I cannot make it to that meeting.\n\n-----Original Message-----\nFrom: Alice..."
        assert is_forwarded_email(subject=subj, body=body) is False


class TestStripReplyTrailPreservesForwards:
    """Ensure _strip_reply_trail cleans reply history while preserving forwarded messages."""

    def test_preserves_forwarded_email_body_in_email_client(self):
        """EmailClient._strip_reply_trail should not truncate forwarded message content."""
        client = EmailClient()
        forwarded_body = (
            "für den Kalender\n\n"
            "---------- Forwarded message ---------\n"
            "Von: Frank, Cornelia <cornelia.frank@gradus.uni-stuttgart.de>\n"
            "Date: Mi., 4. Nov. 2026 um 15:07 Uhr\n"
            "Subject: [An alle Studierenden:] Info-Vortrag \"Promovieren\" I Doing a doctorate\n"
            "To: studierende@uni-stuttgart.de\n\n"
            "Liebe Studierende,\n"
            "am 15. November um 14:00 Uhr findet der Info-Vortrag statt."
        )
        subject = 'Fwd: [An alle Studierenden:] Info-Vortrag "Promovieren" I Doing a doctorate'
        result = client._strip_reply_trail(forwarded_body, subject=subject)
        assert "am 15. November um 14:00 Uhr findet der Info-Vortrag statt." in result
        assert "Von: Frank, Cornelia" in result

    def test_preserves_forwarded_email_body_in_exchange_client(self):
        """ExchangeClient._strip_reply_trail should not truncate forwarded message content."""
        with patch.object(ExchangeClient, "_connect"):
            client = ExchangeClient()
            forwarded_body = (
                "FYI\n\n"
                "-----Ursprüngliche Nachricht-----\n"
                "Von: Max Mustermann <max@example.com>\n"
                "Gesendet: Mittwoch, 4. November 2026 10:00\n"
                "An: Team <team@example.com>\n"
                "Betreff: Team Workshop\n\n"
                "Der Workshop ist am Freitag um 10 Uhr im Raum 2.01."
            )
            subject = "WG: Team Workshop"
            result = client._strip_reply_trail(forwarded_body, subject=subject)
            assert "Der Workshop ist am Freitag um 10 Uhr im Raum 2.01." in result
            assert "Von: Max Mustermann" in result

    def test_strips_quoted_reply_in_regular_email(self):
        """Regular reply emails should have previous quoted message trimmed."""
        client = EmailClient()
        reply_body = (
            "Yes, that time works perfectly for me!\n\n"
            "-----Original Message-----\n"
            "From: colleague@example.com\n"
            "Sent: Monday, October 10, 2026 9:00 AM\n"
            "To: me@example.com\n"
            "Subject: Sync meeting\n\n"
            "Can we meet at 10am tomorrow?"
        )
        subject = "Re: Sync meeting"
        result = client._strip_reply_trail(reply_body, subject=subject)
        assert result == "Yes, that time works perfectly for me!"

    def test_does_not_strip_body_to_empty(self):
        """If body begins with quote or marker, should not drop entire content to empty string."""
        client = EmailClient()
        body = (
            "> Can we meet tomorrow at 10am?\n"
            "Yes, sure."
        )
        result = client._strip_reply_trail(body, subject="Sync")
        assert len(result) > 0


class TestRescanPromptingAndDoublePass:
    """Validate rescan prompt additions and Pass 2 double check error resolution."""

    def test_rescan_directive_in_prompt(self):
        """When is_rescan=True, extraction prompt informs LLM that an event is expected."""
        client = LLMClient()
        prompt = client._create_event_extraction_prompt(
            email_body="Announcement about seminar on 2026-11-20 at 14:00.",
            email_subject="Seminar",
            reference_timestamp="2026-11-06T10:00:00Z",
            is_rescan=True,
            rescan_hint="Look for the seminar schedule",
        )
        assert "Rescan Directive & Priority" in prompt
        assert "user explicitly requested a re-scan" in prompt
        assert "User hint: 'Look for the seminar schedule'" in prompt

    def test_rescan_directive_in_verification_prompt(self):
        """When is_rescan=True, verification prompt highlights rescan context."""
        client = LLMClient()
        prompt = client._create_verification_prompt(
            email_body="Announcement about seminar on 2026-11-20 at 14:00.",
            email_subject="Seminar",
            initial_event_data={"has_event": False, "events": []},
            reference_timestamp="2026-11-06T10:00:00Z",
            is_rescan=True,
            rescan_hint="Check forwarded section",
        )
        assert "Rescan Verification Directive" in prompt
        assert "Check forwarded section" in prompt

    def test_double_check_no_event_does_not_raise_unparseable_error(self):
        """Pass 2 double-check concluding has_event: false must return (None, metadata) without raising LLMError."""
        client = LLMClient()

        # Mock LLM calls: Pass 1 returns has_event: false, Pass 2 also returns has_event: false
        no_event_json = json.dumps({"has_event": False, "events": []})

        with patch.object(client, "_call_llm", return_value=(no_event_json, {"raw": "ok"})):
            event_data, metadata = client.analyze_email_for_event(
                email_body="Just saying hello, how are you?",
                email_subject="Casual Hello",
                double_check=True,
                is_rescan=True,
            )
            assert event_data is None
            assert metadata.get("double_check") is True
            assert metadata.get("is_rescan") is True

    def test_double_check_recovers_missed_event(self):
        """When Pass 1 missed the event but Pass 2 double-check finds it, event is returned."""
        client = LLMClient()

        pass1_resp = json.dumps({"has_event": False, "events": []})
        pass2_resp = json.dumps({
            "has_event": True,
            "events": [{
                "title": "Info-Vortrag Promovieren",
                "description": "Information session on doing a doctorate",
                "start_datetime": "2026-11-15T14:00:00+01:00",
                "end_datetime": "2026-11-15T15:00:00+01:00",
                "location": "Online via Webex",
            }],
        })

        with patch.object(client, "_call_llm", side_effect=[
            (pass1_resp, {"pass": 1}),
            (pass2_resp, {"pass": 2}),
        ]):
            event_data, metadata = client.analyze_email_for_event(
                email_body="Fwd: Info-Vortrag Promovieren am 15. November um 14:00 Uhr...",
                email_subject='Fwd: Info-Vortrag "Promovieren"',
                double_check=True,
                is_rescan=True,
            )
            assert event_data is not None
            assert event_data["has_event"] is True
            assert len(event_data["events"]) == 1
            assert event_data["events"][0]["title"] == "Info-Vortrag Promovieren"


class TestWebUIRescanEndpoint:
    """Test API rescan endpoint handles double-check and hints cleanly."""

    def test_api_rescan_event_with_double_check_no_event(self, tmp_path):
        """Rescan with double-check concluding no event should return 200 with no_event record."""
        test_email_id = "msg_test_rescan_123"

        # Record a dummy result in DB to be rescanned
        storage.record_result(
            email_id=test_email_id,
            email_subject='Fwd: Info-Vortrag "Promovieren"',
            email_sender="sender@example.com",
            email_date="2026-11-06T10:00:00Z",
            email_message_id="<test-mid-123@example.com>",
            has_event=False,
            status="no_event",
        )

        dummy_email_data = {
            'id': test_email_id,
            'message_id': '<test-mid-123@example.com>',
            'subject': 'Fwd: Info-Vortrag "Promovieren"',
            'sender': 'sender@example.com',
            'date': '2026-11-06T10:00:00Z',
            'body': 'Info talk on Nov 15 at 14:00',
            'recipients': ['sender@example.com'],
        }

        mock_event_data = {
            'has_event': True,
            'events': [{
                'title': 'Info-Vortrag Promovieren',
                'description': 'Details about doctorate info session',
                'start_datetime': '2026-11-15T14:00:00+01:00',
                'end_datetime': '2026-11-15T15:00:00+01:00',
                'location': 'Room 101',
            }],
        }
        mock_meta = {
            'prompt': 'Prompt text',
            'response_text': json.dumps(mock_event_data),
            'raw_result': {},
            'double_check': True,
            'is_rescan': True,
        }

        with app.test_client() as test_client:
            with patch("config.EMAIL_PROTOCOL", "IMAP"), \
                 patch("web_ui._check_auth", return_value=True), \
                 patch("web_ui._credentials_ready", return_value=True), \
                 patch("email_client.EmailClient.fetch_email_by_id", return_value=dummy_email_data), \
                 patch("llm_client.LLMClient.analyze_email_for_event", return_value=(mock_event_data, mock_meta)) as mock_analyze:

                resp = test_client.post(
                    f"/api/events/{test_email_id}/rescan",
                    json={"double_check": True, "hint": "Look for doctorate talk"},
                )

                assert resp.status_code == 200
                data = resp.get_json()
                assert data["ok"] is True
                assert "Email successfully rescanned with double-check" in data["message"]
                assert data["record"]["status"] == "pending_approval"
                assert data["record"]["event_title"] == "Info-Vortrag Promovieren"

                # Verify analyze_email_for_event was called with is_rescan=True and rescan_hint
                mock_analyze.assert_called_once()
                call_kwargs = mock_analyze.call_args[1]
                assert call_kwargs.get("is_rescan") is True
                assert call_kwargs.get("rescan_hint") == "Look for doctorate talk"
                assert call_kwargs.get("double_check") is True

    def test_api_rescan_event_when_no_event_detected_returns_200_no_event(self):
        """When rescan concludes no event exists, it should update record to no_event without 500 error."""
        test_email_id = "msg_test_rescan_no_event"

        storage.record_result(
            email_id=test_email_id,
            email_subject='Casual chat',
            email_sender="sender@example.com",
            email_date="2026-11-06T10:00:00Z",
            email_message_id="<test-mid-no-event@example.com>",
            has_event=True,
            status="pending_approval",
        )

        dummy_email_data = {
            'id': test_email_id,
            'message_id': '<test-mid-no-event@example.com>',
            'subject': 'Casual chat',
            'sender': 'sender@example.com',
            'date': '2026-11-06T10:00:00Z',
            'body': 'Just chatting, no event here.',
            'recipients': ['sender@example.com'],
        }

        mock_meta = {
            'prompt': 'Prompt text',
            'response_text': json.dumps({"has_event": False, "events": []}),
            'raw_result': {},
            'double_check': True,
            'is_rescan': True,
        }

        with app.test_client() as test_client:
            with patch("config.EMAIL_PROTOCOL", "IMAP"), \
                 patch("web_ui._check_auth", return_value=True), \
                 patch("web_ui._credentials_ready", return_value=True), \
                 patch("email_client.EmailClient.fetch_email_by_id", return_value=dummy_email_data), \
                 patch("llm_client.LLMClient.analyze_email_for_event", return_value=(None, mock_meta)):

                resp = test_client.post(
                    f"/api/events/{test_email_id}/rescan",
                    json={"double_check": True},
                )

                assert resp.status_code == 200
                data = resp.get_json()
                assert data["ok"] is True
                assert data["record"]["status"] == "no_event"
                assert data["record"]["has_event"] is False
