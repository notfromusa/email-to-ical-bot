"""
Configuration file for iCal Bot
Update these settings with your email and LLM configuration
"""

import os
import json
import secrets
import threading
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

from ical_bot.settings import Settings

# Load environment variables from .env file
load_dotenv()

_CONFIG_LOCK = threading.RLock()

# Email Configuration
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "your-email@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_PROTOCOL = os.getenv("EMAIL_PROTOCOL", "IMAP").upper()  # IMAP or EXCHANGE
INTERNAL_EMAIL_DOMAIN = os.getenv("INTERNAL_EMAIL_DOMAIN", "example.com")
ORGANIZER_EMAIL = os.getenv("ORGANIZER_EMAIL", EMAIL_ADDRESS)

# IMAP Configuration (for Gmail, etc.)
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))

# SMTP Configuration (for Gmail, etc.)
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

# Exchange Configuration (for Office365, university emails)
EXCHANGE_SERVER = os.getenv("EXCHANGE_SERVER", "outlook.office365.com")
EXCHANGE_VERSION = os.getenv("EXCHANGE_VERSION", "Exchange2016")  # Exchange2016, Exchange2019, etc.
EXCHANGE_EMAIL = os.getenv("EXCHANGE_EMAIL", EMAIL_ADDRESS)  # Usually same as EMAIL_ADDRESS
EXCHANGE_USERNAME = os.getenv("EXCHANGE_USERNAME", "")  # Optional: username/UPN if different from EXCHANGE_EMAIL
EXCHANGE_AUTODISCOVER = os.getenv("EXCHANGE_AUTODISCOVER", "false").lower() == "true"

# LLM Configuration (Ollama local instance)
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://localhost:11434/api/chat")
LLM_MODEL = os.getenv("LLM_MODEL", "gemma4:26b")  # or any model you have installed
LLM_DEBUG_DIR = os.getenv("LLM_DEBUG_DIR", "")  # empty disables on-disk debug artifacts
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.0"))
LLM_TOP_P = float(os.getenv("LLM_TOP_P", "0.15"))
LLM_MAX_EVENTS_PER_EMAIL = int(os.getenv("LLM_MAX_EVENTS_PER_EMAIL", "5"))
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "300"))
LLM_KEEP_ALIVE = os.getenv("LLM_KEEP_ALIVE", "5m")
LLM_THINK = os.getenv("LLM_THINK", "")  # Optional explicit thinking override ("true", "false", or level); unset preserves model default
LLM_MAX_RETRY_ATTEMPTS = int(os.getenv("LLM_MAX_RETRY_ATTEMPTS", "1"))
LLM_DEFAULT_DURATION_MINUTES = int(os.getenv("LLM_DEFAULT_DURATION_MINUTES", "60"))
LLM_DURATION_RULES_JSON = os.getenv("LLM_DURATION_RULES_JSON", "")
RESULTS_DB_PATH = os.getenv("RESULTS_DB_PATH", "data/processed_results.db")

# System Workload & Opportunistic Execution Configuration
WORKLOAD_MONITOR_ENABLED = os.getenv("WORKLOAD_MONITOR_ENABLED", "true").lower() == "true"
WORKLOAD_VRAM_THRESHOLD_MB = int(os.getenv("WORKLOAD_VRAM_THRESHOLD_MB", "500"))
WORKLOAD_GPU_UTIL_THRESHOLD = int(os.getenv("WORKLOAD_GPU_UTIL_THRESHOLD", "30"))
WORKLOAD_CPU_THRESHOLD_PCT = int(os.getenv("WORKLOAD_CPU_THRESHOLD_PCT", "70"))
WORKLOAD_CPU_SUSTAINED_POLLS = int(os.getenv("WORKLOAD_CPU_SUSTAINED_POLLS", "3"))
WORKLOAD_COOLDOWN_SECONDS = int(os.getenv("WORKLOAD_COOLDOWN_SECONDS", "300"))
WORKLOAD_WHITELISTED_PROCS = os.getenv("WORKLOAD_WHITELISTED_PROCS", "")

# Attachment extraction configuration
ATTACHMENT_PARSING_ENABLED = os.getenv("ATTACHMENT_PARSING_ENABLED", "true").lower() == "true"
ATTACHMENT_MAX_FILE_SIZE_BYTES = int(os.getenv("ATTACHMENT_MAX_FILE_SIZE_BYTES", "5242880"))
ATTACHMENT_MAX_TEXT_CHARS = int(os.getenv("ATTACHMENT_MAX_TEXT_CHARS", "12000"))
ATTACHMENT_MAX_FILES_PER_EMAIL = int(os.getenv("ATTACHMENT_MAX_FILES_PER_EMAIL", "5"))
ATTACHMENT_MAX_PDF_PAGES = int(os.getenv("ATTACHMENT_MAX_PDF_PAGES", "20"))
ATTACHMENT_MAX_UNCOMPRESSED_BYTES = int(os.getenv("ATTACHMENT_MAX_UNCOMPRESSED_BYTES", "10485760"))

# Debug / diagnostics
ENABLE_DEBUG_DUMPS = os.getenv("ENABLE_DEBUG_DUMPS", "false").lower() == "true"

# Web UI Authentication
UI_AUTH_STORE_PATH = os.getenv("UI_AUTH_STORE_PATH", "data/ui_auth.json")
UI_AUTH_SECRET_PATH = os.getenv("UI_AUTH_SECRET_PATH", "data/ui_auth_secret.key")
REQUIRE_HTTPS_FOR_REMOTE = os.getenv("REQUIRE_HTTPS_FOR_REMOTE", "true").lower() == "true"
REQUIRE_HTTPS_FOR_CREDENTIALS = os.getenv("REQUIRE_HTTPS_FOR_CREDENTIALS", "true").lower() == "true"
REQUIRE_HTTPS_FOR_LLM = os.getenv("REQUIRE_HTTPS_FOR_LLM", "true").lower() == "true"
TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "0"))
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
SESSION_LIFETIME_HOURS = int(os.getenv("SESSION_LIFETIME_HOURS", "12"))
WEB_UI_MAX_CONTENT_LENGTH = int(os.getenv("WEB_UI_MAX_CONTENT_LENGTH", "1048576"))

# Processing Configuration
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))  # 5 minutes
PROCESSED_EMAILS_FILE = os.getenv("PROCESSED_EMAILS_FILE", "processed_emails.json")
MAX_EMAILS_PER_CHECK = int(os.getenv("MAX_EMAILS_PER_CHECK", "10"))
DRY_RUN_TARGET_EVENTS = int(os.getenv("DRY_RUN_TARGET_EVENTS", "5"))
AUTO_APPROVE_EVENTS = os.getenv("AUTO_APPROVE_EVENTS", "false").lower() == "true"
ENFORCE_INTERNAL_RECIPIENT_DOMAIN = os.getenv("ENFORCE_INTERNAL_RECIPIENT_DOMAIN", "false").lower() == "true"
AUTO_APPROVE_ALLOW_EXTERNAL_RECIPIENTS = os.getenv("AUTO_APPROVE_ALLOW_EXTERNAL_RECIPIENTS", "false").lower() == "true"

# Calendar Configuration
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Europe/Berlin")
ORGANIZER_NAME = os.getenv("ORGANIZER_NAME", "iCal Bot")
INVITE_SUBJECT_PREFIX = os.getenv("INVITE_SUBJECT_PREFIX", "")
INVITE_SUBJECT_SUFFIX = os.getenv("INVITE_SUBJECT_SUFFIX", " (iCal-Bot)")

# Exchange Direct Calendar Configuration (Option A: native Outlook editability)
# When EMAIL_PROTOCOL=EXCHANGE, creates events directly in the user's Exchange calendar folder
# via EWS CalendarItem.save(), ensuring the user is the organizer and can edit events in Outlook natively.
EXCHANGE_DIRECT_CALENDAR_CREATION = os.getenv("EXCHANGE_DIRECT_CALENDAR_CREATION", "true").lower() == "true"
EXCHANGE_CALENDAR_INVITE_ATTENDEES = os.getenv("EXCHANGE_CALENDAR_INVITE_ATTENDEES", "false").lower() == "true"
EXCHANGE_SEND_CONFIRMATION_EMAIL = os.getenv("EXCHANGE_SEND_CONFIRMATION_EMAIL", "false").lower() == "true"
CALENDAR_ITEM_CATEGORY = os.getenv("CALENDAR_ITEM_CATEGORY", "iCal-Bot")

# Testing & Monitoring Configuration
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"  # Set to true to preview without sending emails

# Web UI Configuration
WEB_UI_ENABLED = os.getenv("WEB_UI_ENABLED", "true").lower() == "true"
WEB_UI_HOST = os.getenv("WEB_UI_HOST", "127.0.0.1")
WEB_UI_PORT = int(os.getenv("WEB_UI_PORT", "5000"))
UI_SETTINGS_PATH = os.getenv("UI_SETTINGS_PATH", "data/ui_settings.json")


def write_private_file(path: Path, content: str) -> None:
    """Atomically write sensitive content to disk with owner-only (0600) permissions.

    Creates the parent directory with 0700 permissions if needed, opens a temporary
    sibling file directly with mode 0600 (O_EXCL) to avoid any world-readable window,
    writes UTF-8 encoded content, and atomically replaces the target file.

    Args:
        path: Destination file path.
        content: Text content to write as UTF-8.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass

    data = content.encode("utf-8")
    tmp_path: Optional[Path] = None
    for _ in range(8):
        candidate = target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp"
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            tmp_path = candidate
            break
        except FileExistsError:
            continue
    if tmp_path is None:
        raise RuntimeError(f"Could not allocate temporary file for {target}")

    try:
        try:
            view = memoryview(data)
            written = 0
            while written < len(data):
                written += os.write(fd, view[written:])
        finally:
            os.close(fd)
        os.replace(tmp_path, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _ui_settings_file() -> Path:
    return Path(UI_SETTINGS_PATH)


def _load_ui_settings() -> dict:
    path = _ui_settings_file()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_ui_settings(settings: dict) -> None:
    path = _ui_settings_file()
    write_private_file(path, json.dumps(settings))


def _apply_ui_settings_overrides() -> None:
    global ATTACHMENT_PARSING_ENABLED
    with _CONFIG_LOCK:
        settings = _load_ui_settings()
        value = settings.get('attachment_parsing_enabled')
        if value is None:
            return
        if isinstance(value, bool):
            ATTACHMENT_PARSING_ENABLED = value
            return
        if isinstance(value, str):
            ATTACHMENT_PARSING_ENABLED = value.strip().lower() == 'true'


_apply_ui_settings_overrides()


def get_settings() -> Settings:
    """Return a thread-safe typed :class:`~ical_bot.settings.Settings` snapshot."""
    import sys

    return Settings.from_config_module(sys.modules[__name__])


def has_email_password() -> bool:
    """Return True when a non-placeholder mailbox password is set."""
    placeholder_values = {"your-app-password", "your-password", "change-me"}
    with _CONFIG_LOCK:
        if not EMAIL_PASSWORD:
            return False
        return EMAIL_PASSWORD not in placeholder_values


def set_email_credentials(
    password: str,
    *,
    email_address: Optional[str] = None,
    exchange_username: Optional[str] = None,
) -> None:
    """Thread-safely update runtime mailbox credentials in memory.

    Args:
        password: Mailbox password or app password.
        email_address: Optional replacement mailbox address.
        exchange_username: Optional replacement Exchange login username/UPN.
    """
    global EMAIL_PASSWORD, EMAIL_ADDRESS, EXCHANGE_EMAIL, EXCHANGE_USERNAME
    with _CONFIG_LOCK:
        EMAIL_PASSWORD = password
        if email_address:
            EMAIL_ADDRESS = email_address
            EXCHANGE_EMAIL = email_address
        if exchange_username:
            EXCHANGE_USERNAME = exchange_username


def set_attachment_parsing_enabled(enabled: bool, *, persist: bool = False) -> None:
    """Thread-safely update runtime attachment parsing state and optionally persist it.

    Args:
        enabled: Whether attachment parsing is enabled.
        persist: When True, persist the setting to ``UI_SETTINGS_PATH`` with ``0600`` mode.
    """
    global ATTACHMENT_PARSING_ENABLED
    with _CONFIG_LOCK:
        ATTACHMENT_PARSING_ENABLED = bool(enabled)
        if persist:
            try:
                settings = _load_ui_settings()
                settings['attachment_parsing_enabled'] = ATTACHMENT_PARSING_ENABLED
                _save_ui_settings(settings)
            except Exception:
                pass
