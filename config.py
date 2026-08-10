"""
Configuration file for iCal Bot
Update these settings with your email and LLM configuration
"""

import os
import json
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

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
EXCHANGE_AUTODISCOVER = os.getenv("EXCHANGE_AUTODISCOVER", "true").lower() == "true"

# LLM Configuration (Ollama local instance)
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://localhost:11434/api/generate")
LLM_MODEL = os.getenv("LLM_MODEL", "gemma4:26b")  # or any model you have installed
LLM_DEBUG_DIR = os.getenv("LLM_DEBUG_DIR", "")  # empty disables on-disk debug artifacts
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.0"))
LLM_TOP_P = float(os.getenv("LLM_TOP_P", "0.15"))
LLM_MAX_EVENTS_PER_EMAIL = int(os.getenv("LLM_MAX_EVENTS_PER_EMAIL", "5"))
RESULTS_DB_PATH = os.getenv("RESULTS_DB_PATH", "data/processed_results.db")

# Attachment extraction configuration
ATTACHMENT_PARSING_ENABLED = os.getenv("ATTACHMENT_PARSING_ENABLED", "true").lower() == "true"
ATTACHMENT_MAX_FILE_SIZE_BYTES = int(os.getenv("ATTACHMENT_MAX_FILE_SIZE_BYTES", "5242880"))
ATTACHMENT_MAX_TEXT_CHARS = int(os.getenv("ATTACHMENT_MAX_TEXT_CHARS", "12000"))
ATTACHMENT_MAX_FILES_PER_EMAIL = int(os.getenv("ATTACHMENT_MAX_FILES_PER_EMAIL", "5"))
ATTACHMENT_MAX_PDF_PAGES = int(os.getenv("ATTACHMENT_MAX_PDF_PAGES", "20"))

# Debug / diagnostics
ENABLE_DEBUG_DUMPS = os.getenv("ENABLE_DEBUG_DUMPS", "false").lower() == "true"

# Web UI Authentication
UI_AUTH_STORE_PATH = os.getenv("UI_AUTH_STORE_PATH", "data/ui_auth.json")
UI_AUTH_SECRET_PATH = os.getenv("UI_AUTH_SECRET_PATH", "data/ui_auth_secret.key")
REQUIRE_HTTPS_FOR_REMOTE = os.getenv("REQUIRE_HTTPS_FOR_REMOTE", "true").lower() == "true"
REQUIRE_HTTPS_FOR_CREDENTIALS = os.getenv("REQUIRE_HTTPS_FOR_CREDENTIALS", "true").lower() == "true"
REQUIRE_HTTPS_FOR_LLM = os.getenv("REQUIRE_HTTPS_FOR_LLM", "true").lower() == "true"

# Processing Configuration
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))  # 5 minutes
PROCESSED_EMAILS_FILE = "processed_emails.json"
MAX_EMAILS_PER_CHECK = int(os.getenv("MAX_EMAILS_PER_CHECK", "10"))
DRY_RUN_TARGET_EVENTS = int(os.getenv("DRY_RUN_TARGET_EVENTS", "5"))
AUTO_APPROVE_EVENTS = os.getenv("AUTO_APPROVE_EVENTS", "false").lower() == "true"

# Calendar Configuration
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Europe/Berlin")
ORGANIZER_NAME = os.getenv("ORGANIZER_NAME", "iCal Bot")

# Testing & Monitoring Configuration
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"  # Set to true to preview without sending emails

# Web UI Configuration
WEB_UI_ENABLED = os.getenv("WEB_UI_ENABLED", "true").lower() == "true"
WEB_UI_HOST = os.getenv("WEB_UI_HOST", "127.0.0.1")
WEB_UI_PORT = int(os.getenv("WEB_UI_PORT", "5000"))
UI_SETTINGS_PATH = os.getenv("UI_SETTINGS_PATH", "data/ui_settings.json")


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
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(settings), encoding='utf-8')
	try:
		os.chmod(path, 0o600)
	except Exception:
		pass


def _apply_ui_settings_overrides() -> None:
	global ATTACHMENT_PARSING_ENABLED
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


def has_email_password() -> bool:
	placeholder_values = {"your-app-password", "your-password", "change-me"}
	if not EMAIL_PASSWORD:
		return False
	return EMAIL_PASSWORD not in placeholder_values


def set_email_credentials(password: str, *, email_address: Optional[str] = None, exchange_username: Optional[str] = None) -> None:
	global EMAIL_PASSWORD, EMAIL_ADDRESS, EXCHANGE_EMAIL, EXCHANGE_USERNAME
	EMAIL_PASSWORD = password
	if email_address:
		EMAIL_ADDRESS = email_address
		EXCHANGE_EMAIL = email_address
	if exchange_username:
		EXCHANGE_USERNAME = exchange_username


def set_attachment_parsing_enabled(enabled: bool, *, persist: bool = False) -> None:
	global ATTACHMENT_PARSING_ENABLED
	ATTACHMENT_PARSING_ENABLED = bool(enabled)
	if persist:
		try:
			settings = _load_ui_settings()
			settings['attachment_parsing_enabled'] = ATTACHMENT_PARSING_ENABLED
			_save_ui_settings(settings)
		except Exception:
			pass
