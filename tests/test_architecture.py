"""Regression tests covering the architectural improvements across persistence, mail, runtime, LLM, and Web UI."""

from __future__ import annotations

import json
import threading
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import config
from email_client import EmailClient
from exchange_client import ExchangeClient
from ical_bot.llm import EVENT_EXTRACTION_JSON_SCHEMA, infer_default_duration_minutes, load_duration_rules
from ical_bot.mail import (
    MailAdapter,
    build_calendar_mime_message,
    create_mail_adapter,
    filter_allowed_recipients,
    strip_reply_trail,
)
from ical_bot.runtime import BotRuntime
from ical_bot.settings import Settings
from ical_bot.web import create_app
from identifiers import fingerprint_body, generate_email_id, generate_stable_email_id, normalize_message_id
from llm_client import LLMClient
from main import iCalBot
from storage import StorageManager


# --- 1. Unified SQLite Persistence & Legacy Migration ---


def test_storage_wal_busy_timeout_indexes_and_legacy_migration(tmp_path: Path):
    """Verify SQLite WAL mode, busy_timeout, secondary indexes, and idempotent JSON migration."""
    db_path = tmp_path / "arch.db"
    legacy_json = tmp_path / "processed_emails.json"
    legacy_json.write_text(json.dumps({"processed_ids": ["legacy-1", "legacy-2", "legacy-1"]}), encoding="utf-8")

    storage = StorageManager(db_path=str(db_path))

    with storage._connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        indexes = {
            row[1]
            for row in conn.execute(
                "SELECT type, name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) >= 5000
    assert {
        "idx_processed_results_processed_at",
        "idx_processed_results_status",
        "idx_processed_results_message_id",
        "idx_processed_results_approval",
        "idx_approval_logs_email_id",
        "idx_processed_emails_message_id",
    }.issubset(indexes)

    imported_first = storage.import_legacy_processed_emails(str(legacy_json))
    imported_second = storage.import_legacy_processed_emails(str(legacy_json))
    assert imported_first == 2
    assert imported_second == 0

    storage.mark_email_processed("direct-3", message_id="<Msg-Three@Example.COM>", source_uid="33")
    assert {"legacy-1", "legacy-2", "direct-3"}.issubset(storage.get_processed_email_ids())
    assert "msg-three@example.com" in storage.get_processed_message_ids()

    storage.unmark_email_processed("direct-3")
    assert "direct-3" not in storage.get_processed_email_ids()

    storage.update_imap_sync_state("INBOX", "42", 105)
    sync_state = storage.get_imap_sync_state("INBOX")
    assert sync_state is not None
    assert sync_state["uidvalidity"] == "42"
    assert sync_state["last_uid"] == 105


def test_bot_does_not_rewrite_legacy_processed_emails_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify iCalBot stores processed state in SQLite without writing processed_emails.json."""
    db_path = tmp_path / "bot_state.db"
    legacy_path = tmp_path / "processed_emails.json"
    legacy_path.write_text(json.dumps({"processed_ids": ["legacy-a"]}), encoding="utf-8")

    monkeypatch.setattr(config, "RESULTS_DB_PATH", str(db_path))
    monkeypatch.setattr(config, "PROCESSED_EMAILS_FILE", str(legacy_path))
    monkeypatch.setattr(config, "EMAIL_PROTOCOL", "IMAP")

    storage = StorageManager(db_path=str(db_path))
    email_client = MagicMock()
    email_client.fetch_unprocessed_emails.return_value = ([], False)
    llm_client = MagicMock()

    bot = iCalBot(storage=storage, email_client=email_client, llm_client=llm_client)
    assert "legacy-a" in bot.processed_emails

    legacy_path.unlink()
    bot.run_once()
    assert not legacy_path.exists()


# --- 2. Stable Email IDs & Pre-Body Skip ---


def test_stable_email_id_ignores_body_and_attachment_changes():
    """Verify Message-ID-based IDs stay identical when ATTACHMENT_PARSING_ENABLED toggles."""
    id_with_att = generate_stable_email_id(
        message_id="<Event-42@Mail.Example.COM>",
        timestamp="2026-11-20T10:00:00Z",
        sender="prof@example.com",
        subject="Seminar",
        body_hash=fingerprint_body("Meet tomorrow\n\n[Attachment: syllabus.pdf]"),
    )
    id_without_att = generate_stable_email_id(
        message_id="event-42@mail.example.com",
        timestamp="2026-11-20T10:00:00Z",
        sender="prof@example.com",
        subject="Seminar",
        body_hash=fingerprint_body("Meet tomorrow"),
    )
    assert id_with_att == id_without_att
    assert normalize_message_id("  <Event-42@Mail.Example.COM> ") == "event-42@mail.example.com"

    # Fallback without Message-ID still incorporates body_hash
    no_mid_a = generate_email_id(
        message_id=None,
        timestamp="2026-11-20T10:00:00Z",
        sender="prof@example.com",
        subject="Seminar",
        body_hash=fingerprint_body("Body A"),
    )
    no_mid_b = generate_email_id(
        message_id=None,
        timestamp="2026-11-20T10:00:00Z",
        sender="prof@example.com",
        subject="Seminar",
        body_hash=fingerprint_body("Body B"),
    )
    assert no_mid_a != no_mid_b


def test_imap_uid_header_first_skips_seen_message_before_full_body():
    """Verify EmailClient skips already-processed UIDs via header peek before fetching BODY.PEEK[]."""
    client = EmailClient()
    seen_mid = "<seen-1@example.com>"
    new_mid = "<new-2@example.com>"

    seen_msg = EmailMessage()
    seen_msg["Message-ID"] = seen_mid
    seen_msg["Subject"] = "Old announcement"
    seen_msg["From"] = "sender@example.com"
    seen_msg["Date"] = "Fri, 20 Nov 2026 09:00:00 +0000"
    seen_msg.set_content("Old email body")

    new_msg = EmailMessage()
    new_msg["Message-ID"] = new_mid
    new_msg["Subject"] = "New workshop"
    new_msg["From"] = "sender@example.com"
    new_msg["Date"] = "Fri, 20 Nov 2026 10:00:00 +0000"
    new_msg.set_content("Join us tomorrow at 10:00")

    uid_headers = {
        "101": (
            f"Message-ID: {seen_mid}\r\nSubject: Old announcement\r\n"
            "From: sender@example.com\r\nDate: Fri, 20 Nov 2026 09:00:00 +0000\r\n\r\n"
        ).encode(),
        "102": (
            f"Message-ID: {new_mid}\r\nSubject: New workshop\r\n"
            "From: sender@example.com\r\nDate: Fri, 20 Nov 2026 10:00:00 +0000\r\n\r\n"
        ).encode(),
    }
    full_bodies = {"101": seen_msg.as_bytes(), "102": new_msg.as_bytes()}
    body_fetches = []

    mock_mail = MagicMock()
    mock_mail.select.return_value = ("OK", [b"2"])
    mock_mail.response.return_value = ("UIDVALIDITY", [b"77"])

    def fake_uid(command: str, arg1, arg2):
        if command == "search":
            return ("OK", [b"101 102"])
        if command == "fetch":
            uid_val = arg1
            if "HEADER.FIELDS" in arg2:
                return ("OK", [(b"1 (UID ...)", uid_headers[uid_val])])
            if arg2 == "(BODY.PEEK[])":
                body_fetches.append(uid_val)
                return ("OK", [(b"2 (BODY[] ...)", full_bodies[uid_val])])
        return ("OK", [])

    mock_mail.uid.side_effect = fake_uid

    with patch.object(client, "connect_imap", return_value=mock_mail):
        batch, has_more = client.fetch_unprocessed_emails(
            processed_ids=set(),
            max_emails=10,
            processed_message_ids={normalize_message_id(seen_mid)},
        )

    assert has_more is False
    assert body_fetches == ["102"]
    assert len(batch) == 1
    assert batch[0]["uid"] == "102"
    assert batch[0]["uidvalidity"] == "77"
    assert batch[0]["subject"] == "New workshop"


def test_exchange_pre_attachment_skip_avoids_extracting_seen_items():
    """Verify ExchangeClient skips already-processed items before attachment extraction."""
    with patch.object(ExchangeClient, "_connect", return_value=None):
        client = ExchangeClient()
    client.email_address = "bot@example.com"

    fake_dt = MagicMock()
    fake_dt.isoformat.return_value = "2026-11-20T10:00:00+00:00"
    seen_item = SimpleNamespace(
        message_id="<Seen-Exch@example.com>",
        subject="Already processed",
        sender=SimpleNamespace(email_address="a@example.com"),
        datetime_received=fake_dt,
        text_body="Old body",
        html_body="",
        reply_to=[],
        to_recipients=[],
        cc_recipients=[],
    )
    new_item = SimpleNamespace(
        message_id="<New-Exch@example.com>",
        subject="Fresh invite",
        sender=SimpleNamespace(email_address="b@example.com"),
        datetime_received=fake_dt,
        text_body="Meet at 15:00",
        html_body="",
        reply_to=[],
        to_recipients=[SimpleNamespace(email_address="bot@example.com")],
        cc_recipients=[],
    )

    class _FakeQuerySet:
        def order_by(self, *_args):
            return [seen_item, new_item]

    fake_account = SimpleNamespace(inbox=SimpleNamespace(all=lambda: _FakeQuerySet()))
    client.account = fake_account

    with patch.object(client.attachment_extractor, "extract_from_exchange_item", return_value="") as mock_ext:
        batch, has_more = client.fetch_unprocessed_emails(
            processed_ids=set(),
            max_emails=10,
            processed_message_ids={normalize_message_id("<Seen-Exch@example.com>")},
        )

    assert has_more is False
    assert len(batch) == 1
    assert batch[0]["subject"] == "Fresh invite"
    mock_ext.assert_called_once_with(new_item)


# --- 3. MailAdapter Protocol & Shared Mail Helpers ---


def test_mail_adapter_protocol_and_shared_mime_helpers(monkeypatch: pytest.MonkeyPatch):
    """Verify MailAdapter protocol satisfaction, factory routing, and MIME composition helpers."""
    assert isinstance(EmailClient(), MailAdapter)
    with patch.object(ExchangeClient, "_connect", return_value=None):
        assert isinstance(ExchangeClient(), MailAdapter)
        assert isinstance(create_mail_adapter("IMAP"), EmailClient)
        assert isinstance(create_mail_adapter("EXCHANGE"), ExchangeClient)

    clean = strip_reply_trail("Sure, Friday 10:00 works.\n\nOn Thu, Nov 19 wrote:\n> old thread", subject="Re: Sync")
    assert clean == "Sure, Friday 10:00 works."

    monkeypatch.setattr(config, "ENFORCE_INTERNAL_RECIPIENT_DOMAIN", True)
    monkeypatch.setattr(config, "INTERNAL_EMAIL_DOMAIN", "example.edu")
    allowed = filter_allowed_recipients(
        ["Alice <alice@example.edu>", "evil@outside.org", "bad\r\ninject@example.edu", "alice@example.edu"],
        self_email="bot@helper.com",
    )
    assert allowed == ["alice@example.edu"]

    msg = build_calendar_mime_message(
        sender="bot@example.edu",
        recipients=allowed,
        subject="Calendar Invite: Sync",
        reply_message="Added to your calendar.",
        ics_content="BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR",
        in_reply_to="<parent-1@example.edu>\r\nInjected: bad",
    )
    assert msg["In-Reply-To"] == "<parent-1@example.edu>Injected: bad"
    assert "\r" not in msg["In-Reply-To"] and "\n" not in msg["In-Reply-To"]
    part_types = [part.get_content_type() for part in msg.walk()]
    assert "text/calendar" in part_types


# --- 4. Settings, Application Factory, & BotRuntime Single Coordinator ---


def test_settings_create_app_isolation_and_bot_runtime_single_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify typed Settings, isolated create_app(), and single-flight BotRuntime locking."""
    monkeypatch.setenv("LLM_ENDPOINT", "http://127.0.0.1:11434/api/chat")
    monkeypatch.setenv("LLM_DEFAULT_DURATION_MINUTES", "45")
    loaded = Settings.from_env()
    assert loaded.llm_endpoint == "http://127.0.0.1:11434/api/chat"
    assert loaded.llm_default_duration_minutes == 45

    storage = StorageManager(db_path=str(tmp_path / "factory.db"))
    auth_file = tmp_path / "ui_auth.json"
    auth_file.write_text(json.dumps({"username": "op", "password_hash": "h", "must_change": False}))
    monkeypatch.setattr(config, "UI_AUTH_STORE_PATH", str(auth_file))

    entered = threading.Event()
    release = threading.Event()
    run_calls = 0

    class _BlockingBot:
        def run_once(self) -> None:
            nonlocal run_calls
            run_calls += 1
            entered.set()
            release.wait(timeout=2.0)

    runtime = BotRuntime(storage=storage, bot_factory=_BlockingBot)
    app = create_app(settings=loaded, storage=storage, runtime=runtime, start_bot=False)
    assert set(app.blueprints.keys()) >= {"auth", "events", "manual", "admin"}

    worker = threading.Thread(target=runtime.run_check_once, kwargs={"blocking": True}, daemon=True)
    worker.start()
    assert entered.wait(timeout=1.5)
    assert runtime.is_checking is True
    # Non-blocking concurrent check must be rejected by the coordinator lock
    assert runtime.run_check_once(blocking=False) is False
    release.set()
    worker.join(timeout=2.0)

    assert run_calls == 1
    assert runtime.last_check_at is not None
    runtime.invalidate_bot()
    assert runtime._credentials_dirty is True


# --- 5. Ollama /api/chat + JSON Schema + Configurable Duration Rules ---


def test_llm_chat_schema_and_generate_backcompat(monkeypatch: pytest.MonkeyPatch):
    """Verify /api/chat uses messages + JSON schema while /api/generate stays compatible."""
    client = LLMClient()
    client.endpoint = "http://localhost:11434/api/chat"

    chat_resp = MagicMock()
    chat_resp.raise_for_status.return_value = None
    chat_resp.json.return_value = {"message": {"content": '{"has_event": false, "events": []}'}}

    with patch("requests.post", return_value=chat_resp) as mock_post:
        text, _ = client._call_llm(
            "User prompt",
            force_json_format=True,
            system_prompt="System instructions",
            json_schema=EVENT_EXTRACTION_JSON_SCHEMA,
        )
        payload = mock_post.call_args.kwargs["json"]
        assert payload["messages"][0] == {"role": "system", "content": "System instructions"}
        assert payload["messages"][1] == {"role": "user", "content": "User prompt"}
        assert payload["format"] == EVENT_EXTRACTION_JSON_SCHEMA
        assert text == '{"has_event": false, "events": []}'

    client.endpoint = "http://localhost:11434/api/generate"
    gen_resp = MagicMock()
    gen_resp.raise_for_status.return_value = None
    gen_resp.json.return_value = {"response": '{"has_event": false, "events": []}'}

    with patch("requests.post", return_value=gen_resp) as mock_gen_post:
        text, _ = client._call_llm("Legacy prompt", force_json_format=True, system_prompt="Sys")
        gen_payload = mock_gen_post.call_args.kwargs["json"]
        assert gen_payload["prompt"] == "Legacy prompt"
        assert gen_payload["system"] == "Sys"
        assert gen_payload["format"] == "json"
        assert text == '{"has_event": false, "events": []}'


def test_configurable_duration_rules(monkeypatch: pytest.MonkeyPatch):
    """Verify LLM_DURATION_RULES_JSON and LLM_DEFAULT_DURATION_MINUTES customize inference."""
    rules_json = json.dumps([
        {"name": "lightning_talk", "duration_minutes": 15, "keywords": ["lightning talk"]},
    ])
    monkeypatch.setattr(config, "LLM_DURATION_RULES_JSON", rules_json)
    monkeypatch.setattr(config, "LLM_DEFAULT_DURATION_MINUTES", 45)

    rules = load_duration_rules()
    assert len(rules) == 1
    assert infer_default_duration_minutes("Friday Lightning Talk", "Quick demo") == 15
    assert infer_default_duration_minutes("Project Sync", "Monthly check-in") == 45


# --- 6. Python Time Hygiene ---


def test_no_deprecated_datetime_utcnow_in_production_code():
    """Verify datetime.utcnow() has been completely eliminated from application Python modules."""
    repo_root = Path(__file__).resolve().parent.parent
    targets = list((repo_root / "src").rglob("*.py")) + list(repo_root.glob("*.py"))
    offenders = [
        str(path.relative_to(repo_root))
        for path in targets
        if "datetime.utcnow(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
