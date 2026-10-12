"""Unit tests for LLM error handling, retry mechanisms, and recovery of null responses."""

import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
import requests

from llm_client import LLMClient, LLMError
from main import iCalBot, ProcessingResult
from storage import StorageManager


def test_llm_client_timeout_raises_llm_error():
    """Verify that a requests.exceptions.Timeout causes LLMClient to raise LLMError."""
    client = LLMClient()
    client.timeout = 10

    with patch("requests.post", side_effect=requests.exceptions.Timeout("Read timed out")):
        with pytest.raises(LLMError) as exc_info:
            client.analyze_email_for_event(
                email_body="Let's meet tomorrow at 10am",
                email_subject="Meeting",
            )
        assert "timed out" in str(exc_info.value).lower()
        assert exc_info.value.metadata.get("prompt") is not None


def test_llm_client_payload_configuration():
    """Verify that timeout, keep_alive, and model settings are sent in requests."""
    client = LLMClient()
    client.timeout = 120
    client.keep_alive = "45m"
    client.think = ""

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "response": json.dumps({"has_event": False, "events": []})
    }
    mock_resp.raise_for_status.return_value = None

    with patch("requests.post", return_value=mock_resp) as mock_post:
        data, meta = client.analyze_email_for_event(
            email_body="Hello world",
            email_subject="Greeting",
        )
        assert data is None
        assert mock_post.called
        kwargs = mock_post.call_args[1]
        assert kwargs["timeout"] == 120
        payload = kwargs["json"]
        assert payload["keep_alive"] == "45m"
        assert "think" not in payload  # default preserves model native setting


def test_process_email_llm_error_retry_logic(tmp_path):
    """Verify that an LLM error logs warning and allows retry once before marking processed."""
    db_file = tmp_path / "test_results.db"
    processed_file = tmp_path / "processed.json"

    with patch("config.RESULTS_DB_PATH", str(db_file)), \
         patch("config.PROCESSED_EMAILS_FILE", str(processed_file)), \
         patch("config.EMAIL_PROTOCOL", "IMAP"), \
         patch("config.LLM_MAX_RETRY_ATTEMPTS", 1):
        
        bot = iCalBot()
        email_data = {
            "id": "email-123",
            "subject": "Team Sync",
            "sender": "alice@example.com",
            "body": "Let's sync up tomorrow",
            "date": "2026-10-12T10:00:00Z",
        }

        # First attempt: LLM fails with timeout
        with patch.object(bot.llm_client, "analyze_email_for_event", side_effect=LLMError("Simulated LLM Timeout")):
            result = bot.process_email(email_data)
            assert isinstance(result, ProcessingResult)
            assert bool(result) is False
            assert result.status == "llm_error"
            # First failure: should NOT mark processed so it can be retried
            assert result.mark_processed is False

            # Verify recorded in DB as llm_error
            stored = bot.storage.get_result("email-123")
            assert stored is not None
            assert stored["status"] == "llm_error"
            assert stored["llm_error_count"] == 1
            assert "Simulated LLM Timeout" in stored["error"]

        # Second attempt (retry cycle): LLM fails again
        with patch.object(bot.llm_client, "analyze_email_for_event", side_effect=LLMError("Simulated LLM Timeout 2")):
            result_second = bot.process_email(email_data)
            assert result_second.status == "llm_error"
            # Second failure: retry limit reached (1 retry), so mark_processed is now True
            assert result_second.mark_processed is True

            stored_second = bot.storage.get_result("email-123")
            assert stored_second["llm_error_count"] == 2


def test_storage_recover_null_llm_results(tmp_path):
    """Verify that recover_null_llm_results identifies null responses and updates them to llm_error."""
    db_file = tmp_path / "test_recovery.db"
    storage = StorageManager(str(db_file))

    # Insert a record that had a null response (the bug situation)
    storage.record_result(
        email_id="buggy-1",
        email_subject="Test Null Response",
        email_sender="sender@example.com",
        email_date="2026-10-10",
        has_event=False,
        status="no_event_detected",
        llm_prompt="Extract events from this...",
        llm_response=None,
    )

    # Insert a valid no-event record with actual response
    storage.record_result(
        email_id="valid-1",
        email_subject="Valid Non-Event",
        email_sender="sender@example.com",
        email_date="2026-10-10",
        has_event=False,
        status="no_event_detected",
        llm_prompt="Extract events from this...",
        llm_response='{"has_event": false, "events": []}',
    )

    recovered = storage.recover_null_llm_results()
    assert recovered == ["buggy-1"]

    rec = storage.get_result("buggy-1")
    assert rec["status"] == "llm_error"
    assert rec["llm_error_count"] == 0

    valid_rec = storage.get_result("valid-1")
    assert valid_rec["status"] == "no_event_detected"
