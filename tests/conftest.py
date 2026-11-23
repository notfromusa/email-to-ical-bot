"""Pytest configuration and shared isolation fixtures for iCal Bot tests."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Optional

import pytest

_SESSION_TEMP_DIR: Optional[Path] = None
_SAVED_ENV: Dict[str, Optional[str]] = {}
_ISOLATED_ENV_VARS = (
    "RESULTS_DB_PATH",
    "PROCESSED_EMAILS_FILE",
    "UI_AUTH_STORE_PATH",
    "UI_AUTH_SECRET_PATH",
    "UI_SETTINGS_PATH",
    "LLM_DEBUG_DIR",
)


def pytest_configure(config: pytest.Config) -> None:
    """Redirect persistent file env vars to a session temp directory before imports."""
    del config
    global _SESSION_TEMP_DIR
    _SESSION_TEMP_DIR = Path(tempfile.mkdtemp(prefix="ical_bot_pytest_"))
    defaults = {
        "RESULTS_DB_PATH": str(_SESSION_TEMP_DIR / "session_results.db"),
        "PROCESSED_EMAILS_FILE": str(_SESSION_TEMP_DIR / "session_processed.json"),
        "UI_AUTH_STORE_PATH": str(_SESSION_TEMP_DIR / "session_ui_auth.json"),
        "UI_AUTH_SECRET_PATH": str(_SESSION_TEMP_DIR / "session_ui_auth_secret.key"),
        "UI_SETTINGS_PATH": str(_SESSION_TEMP_DIR / "session_ui_settings.json"),
        "LLM_DEBUG_DIR": "",
    }
    for key in _ISOLATED_ENV_VARS:
        _SAVED_ENV[key] = os.environ.get(key)
        os.environ[key] = defaults[key]


def pytest_unconfigure(config: pytest.Config) -> None:
    """Restore original environment variables and remove the session temp directory."""
    del config
    for key, previous in _SAVED_ENV.items():
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous
    if _SESSION_TEMP_DIR is not None and _SESSION_TEMP_DIR.exists():
        shutil.rmtree(_SESSION_TEMP_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolate_test_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate every test's SQLite database, auth store, secret key, and settings in ``tmp_path``."""
    import config
    import web_ui

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    db_path = state_dir / "test_results.db"
    processed_path = state_dir / "processed_emails.json"
    auth_path = state_dir / "ui_auth.json"
    secret_path = state_dir / "ui_auth_secret.key"
    settings_path = state_dir / "ui_settings.json"

    monkeypatch.setattr(config, "RESULTS_DB_PATH", str(db_path))
    monkeypatch.setattr(config, "PROCESSED_EMAILS_FILE", str(processed_path))
    monkeypatch.setattr(config, "UI_AUTH_STORE_PATH", str(auth_path))
    monkeypatch.setattr(config, "UI_AUTH_SECRET_PATH", str(secret_path))
    monkeypatch.setattr(config, "UI_SETTINGS_PATH", str(settings_path))

    monkeypatch.setenv("RESULTS_DB_PATH", str(db_path))
    monkeypatch.setenv("PROCESSED_EMAILS_FILE", str(processed_path))
    monkeypatch.setenv("UI_AUTH_STORE_PATH", str(auth_path))
    monkeypatch.setenv("UI_AUTH_SECRET_PATH", str(secret_path))
    monkeypatch.setenv("UI_SETTINGS_PATH", str(settings_path))

    auth_path.write_text(
        json.dumps({"username": "test-admin", "password_hash": "test-hash", "must_change": False}),
        encoding="utf-8",
    )
    os.chmod(auth_path, 0o600)

    web_ui.storage.rebind(str(db_path))
    yield


@pytest.fixture(autouse=True)
def reset_workload_monitor_state(monkeypatch: pytest.MonkeyPatch):
    """Ensure clean, host-isolated workload monitor state for every test."""
    from workload_monitor import get_workload_monitor

    monitor = get_workload_monitor()
    monkeypatch.setattr(monitor, "enabled", False)
    monitor._cooldown_until = 0.0
    monitor._cooldown_reason = ""
    monitor._cpu_high_count = 0
    monitor._prev_cpu_total = 0
    monitor._prev_cpu_idle = 0
    yield
    monitor._cooldown_until = 0.0
    monitor._cooldown_reason = ""
    monitor._cpu_high_count = 0
    monitor._prev_cpu_total = 0
    monitor._prev_cpu_idle = 0
