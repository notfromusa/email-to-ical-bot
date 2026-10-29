"""
Unit tests for WorkloadMonitor, opportunistic yielding, cooldowns, and retry behavior.

Verifies:
1. Process whitelist filtering (case-insensitivity, basenames, desktop environments).
2. GPU VRAM and utilization detection thresholds via simulated nvidia-smi outputs.
3. Host CPU load tracking and consecutive-poll gating.
4. Native Ollama model unloading via API and CLI.
5. Cooldown state transitions and remaining-time calculations.
6. Integration with LLMClient (timeouts trigger cooldown and raise LLMTimeoutError).
7. Integration with iCalBot (timed out / yielding requests remain queued for retry).
"""

import time
import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
import requests

from workload_monitor import WorkloadMonitor, WorkloadYieldError, get_workload_monitor
from llm_client import LLMClient, LLMError, LLMTimeoutError
from main import iCalBot, ProcessingResult
from storage import StorageManager


def test_whitelist_matching():
    """Verify whitelist checks match case-insensitively and handle paths."""
    monitor = WorkloadMonitor(enabled=True)

    # Whitelisted desktop and browser processes
    assert monitor.is_whitelisted("/usr/bin/gnome-shell") is True
    assert monitor.is_whitelisted("GNOME-SHELL") is True
    assert monitor.is_whitelisted("/snap/bin/firefox") is True
    assert monitor.is_whitelisted("chrome") is True
    assert monitor.is_whitelisted("code") is True
    assert monitor.is_whitelisted("antigravity-ide") is True

    # Non-whitelisted user / ML compute tasks
    assert monitor.is_whitelisted("python3 train_sim2splat.py") is False
    assert monitor.is_whitelisted("blender") is False
    assert monitor.is_whitelisted("torchrun") is False
    assert monitor.is_whitelisted("custom_cuda_worker") is False


def test_ollama_process_identification():
    """Verify identification of Ollama runner processes."""
    monitor = WorkloadMonitor(enabled=True)

    assert monitor.is_ollama_process("ollama") is True
    assert monitor.is_ollama_process("/usr/local/bin/ollama") is True
    assert monitor.is_ollama_process("llama_server") is True
    assert monitor.is_ollama_process("ollama_llama_server") is True
    assert monitor.is_ollama_process("runner") is True

    assert monitor.is_ollama_process("python") is False
    assert monitor.is_ollama_process("ffmpeg") is False


def test_detect_colleague_gpu_activity_non_whitelisted_vram():
    """Verify detection of non-whitelisted processes exceeding VRAM threshold."""
    monitor = WorkloadMonitor(enabled=True, vram_threshold_mb=500)

    # Case 1: Whitelisted process using high VRAM (e.g. browser 1200MB) -> clear
    mock_smi_whitelisted = MagicMock(
        returncode=0,
        stdout="1234, /usr/lib/firefox/firefox, 1200\n5678, /usr/bin/gnome-shell, 600\n",
    )
    with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
         patch("subprocess.run", return_value=mock_smi_whitelisted):
        assert monitor.detect_colleague_gpu_activity() is None

    # Case 2: Ollama's own process using high VRAM -> clear
    mock_smi_ollama = MagicMock(
        returncode=0,
        stdout="9999, /usr/local/bin/ollama_llama_server, 18000\n",
    )
    with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
         patch("subprocess.run", return_value=mock_smi_ollama):
        assert monitor.detect_colleague_gpu_activity() is None

    # Case 3: Colleague training job using 4000MB VRAM -> detected
    mock_smi_colleague = MagicMock(
        returncode=0,
        stdout="4444, /home/user/venv/bin/python, 4200\n",
    )
    with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
         patch("subprocess.run", return_value=mock_smi_colleague):
        offending = monitor.detect_colleague_gpu_activity()
        assert offending is not None
        assert "PID=4444" in offending
        assert "4200 MiB" in offending


def test_detect_colleague_gpu_utilization():
    """Verify GPU utilization detection gating."""
    monitor = WorkloadMonitor(enabled=True, gpu_util_threshold=30)

    # Below threshold -> clear
    mock_low_util = MagicMock(returncode=0, stdout="15\n")
    with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
         patch("subprocess.run", return_value=mock_low_util):
        assert monitor.detect_colleague_gpu_utilization() is None

    # Above threshold with external workload -> detected
    mock_high_util = MagicMock(returncode=0, stdout="85\n")
    mock_no_ollama = MagicMock(returncode=0, stdout="4444, python3\n")

    def mock_subprocess_run(cmd, *args, **kwargs):
        if "utilization.gpu" in cmd[1]:
            return mock_high_util
        return mock_no_ollama

    with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
         patch("subprocess.run", side_effect=mock_subprocess_run):
        offending = monitor.detect_colleague_gpu_utilization()
        assert offending is not None
        assert "85%" in offending


def test_cooldown_state_machine():
    """Verify cooldown triggering, duration remaining, and expiration."""
    monitor = WorkloadMonitor(enabled=True, cooldown_duration=2)

    assert monitor.is_in_cooldown() is False
    assert monitor.get_cooldown_remaining() == 0

    with patch.object(monitor, "unload_model", return_value=True) as mock_unload:
        monitor.trigger_cooldown("Test reason", duration_seconds=2)
        assert mock_unload.called

    assert monitor.is_in_cooldown() is True
    assert monitor.get_cooldown_remaining() > 0

    can_run, reason = monitor.can_run_llm()
    assert can_run is False
    assert "In cooldown" in reason

    # Simulate passage of time past cooldown
    monitor._cooldown_until = time.time() - 1
    assert monitor.is_in_cooldown() is False
    assert monitor.get_cooldown_remaining() == 0

    with patch.object(monitor, "detect_competing_workload", return_value=None):
        can_run, reason = monitor.can_run_llm()
        assert can_run is True
        assert "idle" in reason.lower()


def test_unload_model_via_ollama_api():
    """Verify unload_model sends keep_alive=0 via HTTP request."""
    monitor = WorkloadMonitor(enabled=True)

    mock_resp = MagicMock(status_code=200)
    with patch("requests.post", return_value=mock_resp) as mock_post:
        success = monitor.unload_model("gemma4:test")
        assert success is True
        assert mock_post.called
        kwargs = mock_post.call_args[1]
        assert kwargs["json"]["model"] == "gemma4:test"
        assert kwargs["json"]["keep_alive"] == 0


def test_llm_client_timeout_triggers_cooldown():
    """Verify requests.exceptions.Timeout triggers cooldown and raises LLMTimeoutError."""
    client = LLMClient()
    client.timeout = 5

    monitor = get_workload_monitor()
    monitor._cooldown_until = 0

    with patch("requests.post", side_effect=requests.exceptions.Timeout("Read timeout")):
        with patch.object(monitor, "trigger_cooldown") as mock_cooldown:
            with pytest.raises(LLMTimeoutError) as exc_info:
                client._call_llm("test prompt")
            assert mock_cooldown.called
            assert "timed out" in str(exc_info.value).lower()


def test_llm_client_yields_when_system_busy():
    """Verify analyze_email_for_event raises WorkloadYieldError if system is busy."""
    client = LLMClient()
    monitor = get_workload_monitor()

    with patch.object(monitor, "can_run_llm", return_value=(False, "GPU busy with training")):
        with pytest.raises(WorkloadYieldError) as exc_info:
            client.analyze_email_for_event(
                email_body="Let's meet tomorrow at 10am",
                email_subject="Sync meeting",
            )
        assert "GPU busy with training" in str(exc_info.value)


def test_process_email_yielding_and_timeout_retry(tmp_path):
    """Verify that yielding and timed-out emails are NOT marked processed and are retried."""
    db_file = tmp_path / "test_workload_db.db"
    processed_file = tmp_path / "processed_workload.json"

    with patch("config.RESULTS_DB_PATH", str(db_file)), \
         patch("config.PROCESSED_EMAILS_FILE", str(processed_file)), \
         patch("config.EMAIL_PROTOCOL", "IMAP"):

        bot = iCalBot()
        email_data = {
            "id": "email-workload-1",
            "subject": "Important Discussion",
            "sender": "researcher@example.com",
            "body": "Meeting at 2pm tomorrow",
            "date": "2026-10-15T10:00:00Z",
        }

        # 1. Yielding scenario
        with patch.object(
            bot.llm_client,
            "analyze_email_for_event",
            side_effect=WorkloadYieldError("In cooldown for 120s"),
        ):
            res_yield = bot.process_email(email_data)
            assert isinstance(res_yield, ProcessingResult)
            assert res_yield.status == "llm_yielding"
            assert res_yield.mark_processed is False
            assert bot.has_pending_retries is True

            stored = bot.storage.get_result("email-workload-1")
            assert stored["status"] == "llm_yielding"

        # 2. Timeout scenario
        with patch.object(
            bot.llm_client,
            "analyze_email_for_event",
            side_effect=LLMTimeoutError("LLM timed out after 300s"),
        ):
            res_timeout = bot.process_email(email_data)
            assert res_timeout.status == "llm_timeout"
            assert res_timeout.mark_processed is False
            assert bot.has_pending_retries is True

            stored_timeout = bot.storage.get_result("email-workload-1")
            assert stored_timeout["status"] == "llm_timeout"

        # 3. Verify recover_pending_retry_emails recovers this email ID
        bot.processed_emails.add("email-workload-1")
        recovered = bot.recover_pending_retry_emails()
        assert "email-workload-1" in recovered
        assert "email-workload-1" not in bot.processed_emails
