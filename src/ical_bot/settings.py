"""Typed runtime configuration model for iCal Bot."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Optional

_PLACEHOLDER_PASSWORDS = frozenset({"your-app-password", "your-password", "change-me"})


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable case-insensitively."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() == "true"


def _env_int(name: str, default: int) -> int:
    """Parse an integer environment variable with fallback default."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    """Parse a floating-point environment variable with fallback default."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


@dataclass
class Settings:
    """Typed snapshot of iCal Bot configuration values.

    This module provides an explicit configuration interface for downstream
    modules (`create_app`, `BotRuntime`, mail adapters, and workflows) without
    requiring them to mutate or scatter direct reads across global state.
    """

    # Email configuration
    email_address: str = "your-email@gmail.com"
    email_password: str = ""
    email_protocol: str = "IMAP"
    internal_email_domain: str = "example.com"
    organizer_email: str = "your-email@gmail.com"

    # IMAP / SMTP configuration
    imap_server: str = "imap.gmail.com"
    imap_port: int = 993
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587

    # Exchange configuration
    exchange_server: str = "outlook.office365.com"
    exchange_version: str = "Exchange2016"
    exchange_email: str = "your-email@gmail.com"
    exchange_username: str = ""
    exchange_autodiscover: bool = False
    exchange_direct_calendar_creation: bool = True
    exchange_calendar_invite_attendees: bool = False
    exchange_send_confirmation_email: bool = False
    calendar_item_category: str = "iCal-Bot"

    # LLM configuration
    llm_endpoint: str = "http://localhost:11434/api/chat"
    llm_model: str = "gemma4:26b"
    llm_debug_dir: str = ""
    llm_temperature: float = 0.0
    llm_top_p: float = 0.15
    llm_max_events_per_email: int = 5
    llm_timeout_seconds: int = 300
    llm_keep_alive: str = "5m"
    llm_think: str = ""
    llm_max_retry_attempts: int = 1
    llm_default_duration_minutes: int = 60
    llm_duration_rules_json: str = ""

    # Storage & persistence paths
    results_db_path: str = "data/processed_results.db"
    processed_emails_file: str = "processed_emails.json"
    ui_auth_store_path: str = "data/ui_auth.json"
    ui_auth_secret_path: str = "data/ui_auth_secret.key"
    ui_settings_path: str = "data/ui_settings.json"

    # System workload monitor configuration
    workload_monitor_enabled: bool = True
    workload_vram_threshold_mb: int = 500
    workload_gpu_util_threshold: int = 30
    workload_cpu_threshold_pct: int = 70
    workload_cpu_sustained_polls: int = 3
    workload_cooldown_seconds: int = 300
    workload_whitelisted_procs: str = ""

    # Attachment extraction configuration
    attachment_parsing_enabled: bool = True
    attachment_max_file_size_bytes: int = 5_242_880
    attachment_max_text_chars: int = 12_000
    attachment_max_files_per_email: int = 5
    attachment_max_pdf_pages: int = 20
    attachment_max_uncompressed_bytes: int = 10_485_760

    # Diagnostics & HTTP security
    enable_debug_dumps: bool = False
    require_https_for_remote: bool = True
    require_https_for_credentials: bool = True
    require_https_for_llm: bool = True
    trusted_proxy_count: int = 0
    session_cookie_secure: bool = False
    session_lifetime_hours: int = 12
    web_ui_max_content_length: int = 1_048_576

    # Processing & approval configuration
    check_interval_seconds: int = 300
    max_emails_per_check: int = 10
    dry_run_target_events: int = 5
    auto_approve_events: bool = False
    enforce_internal_recipient_domain: bool = False
    auto_approve_allow_external_recipients: bool = False
    dry_run: bool = False

    # Calendar formatting
    default_timezone: str = "Europe/Berlin"
    organizer_name: str = "iCal Bot"
    invite_subject_prefix: str = ""
    invite_subject_suffix: str = " (iCal-Bot)"

    # Web UI server configuration
    web_ui_enabled: bool = True
    web_ui_host: str = "127.0.0.1"
    web_ui_port: int = 5000

    def has_email_password(self) -> bool:
        """Return True when a non-placeholder mailbox password is configured."""
        return bool(self.email_password) and self.email_password not in _PLACEHOLDER_PASSWORDS

    @classmethod
    def from_env(cls) -> "Settings":
        """Construct a ``Settings`` instance from the current process environment."""
        email_address = os.getenv("EMAIL_ADDRESS", "your-email@gmail.com")
        return cls(
            email_address=email_address,
            email_password=os.getenv("EMAIL_PASSWORD", ""),
            email_protocol=os.getenv("EMAIL_PROTOCOL", "IMAP").upper(),
            internal_email_domain=os.getenv("INTERNAL_EMAIL_DOMAIN", "example.com"),
            organizer_email=os.getenv("ORGANIZER_EMAIL", email_address),
            imap_server=os.getenv("IMAP_SERVER", "imap.gmail.com"),
            imap_port=_env_int("IMAP_PORT", 993),
            smtp_server=os.getenv("SMTP_SERVER", "smtp.gmail.com"),
            smtp_port=_env_int("SMTP_PORT", 587),
            exchange_server=os.getenv("EXCHANGE_SERVER", "outlook.office365.com"),
            exchange_version=os.getenv("EXCHANGE_VERSION", "Exchange2016"),
            exchange_email=os.getenv("EXCHANGE_EMAIL", email_address),
            exchange_username=os.getenv("EXCHANGE_USERNAME", ""),
            exchange_autodiscover=_env_bool("EXCHANGE_AUTODISCOVER", False),
            exchange_direct_calendar_creation=_env_bool("EXCHANGE_DIRECT_CALENDAR_CREATION", True),
            exchange_calendar_invite_attendees=_env_bool("EXCHANGE_CALENDAR_INVITE_ATTENDEES", False),
            exchange_send_confirmation_email=_env_bool("EXCHANGE_SEND_CONFIRMATION_EMAIL", False),
            calendar_item_category=os.getenv("CALENDAR_ITEM_CATEGORY", "iCal-Bot"),
            llm_endpoint=os.getenv("LLM_ENDPOINT", "http://localhost:11434/api/chat"),
            llm_model=os.getenv("LLM_MODEL", "gemma4:26b"),
            llm_debug_dir=os.getenv("LLM_DEBUG_DIR", ""),
            llm_temperature=_env_float("LLM_TEMPERATURE", 0.0),
            llm_top_p=_env_float("LLM_TOP_P", 0.15),
            llm_max_events_per_email=_env_int("LLM_MAX_EVENTS_PER_EMAIL", 5),
            llm_timeout_seconds=_env_int("LLM_TIMEOUT_SECONDS", 300),
            llm_keep_alive=os.getenv("LLM_KEEP_ALIVE", "5m"),
            llm_think=os.getenv("LLM_THINK", ""),
            llm_max_retry_attempts=_env_int("LLM_MAX_RETRY_ATTEMPTS", 1),
            llm_default_duration_minutes=_env_int("LLM_DEFAULT_DURATION_MINUTES", 60),
            llm_duration_rules_json=os.getenv("LLM_DURATION_RULES_JSON", ""),
            results_db_path=os.getenv("RESULTS_DB_PATH", "data/processed_results.db"),
            processed_emails_file=os.getenv("PROCESSED_EMAILS_FILE", "processed_emails.json"),
            ui_auth_store_path=os.getenv("UI_AUTH_STORE_PATH", "data/ui_auth.json"),
            ui_auth_secret_path=os.getenv("UI_AUTH_SECRET_PATH", "data/ui_auth_secret.key"),
            ui_settings_path=os.getenv("UI_SETTINGS_PATH", "data/ui_settings.json"),
            workload_monitor_enabled=_env_bool("WORKLOAD_MONITOR_ENABLED", True),
            workload_vram_threshold_mb=_env_int("WORKLOAD_VRAM_THRESHOLD_MB", 500),
            workload_gpu_util_threshold=_env_int("WORKLOAD_GPU_UTIL_THRESHOLD", 30),
            workload_cpu_threshold_pct=_env_int("WORKLOAD_CPU_THRESHOLD_PCT", 70),
            workload_cpu_sustained_polls=_env_int("WORKLOAD_CPU_SUSTAINED_POLLS", 3),
            workload_cooldown_seconds=_env_int("WORKLOAD_COOLDOWN_SECONDS", 300),
            workload_whitelisted_procs=os.getenv("WORKLOAD_WHITELISTED_PROCS", ""),
            attachment_parsing_enabled=_env_bool("ATTACHMENT_PARSING_ENABLED", True),
            attachment_max_file_size_bytes=_env_int("ATTACHMENT_MAX_FILE_SIZE_BYTES", 5_242_880),
            attachment_max_text_chars=_env_int("ATTACHMENT_MAX_TEXT_CHARS", 12_000),
            attachment_max_files_per_email=_env_int("ATTACHMENT_MAX_FILES_PER_EMAIL", 5),
            attachment_max_pdf_pages=_env_int("ATTACHMENT_MAX_PDF_PAGES", 20),
            attachment_max_uncompressed_bytes=_env_int("ATTACHMENT_MAX_UNCOMPRESSED_BYTES", 10_485_760),
            enable_debug_dumps=_env_bool("ENABLE_DEBUG_DUMPS", False),
            require_https_for_remote=_env_bool("REQUIRE_HTTPS_FOR_REMOTE", True),
            require_https_for_credentials=_env_bool("REQUIRE_HTTPS_FOR_CREDENTIALS", True),
            require_https_for_llm=_env_bool("REQUIRE_HTTPS_FOR_LLM", True),
            trusted_proxy_count=_env_int("TRUSTED_PROXY_COUNT", 0),
            session_cookie_secure=_env_bool("SESSION_COOKIE_SECURE", False),
            session_lifetime_hours=_env_int("SESSION_LIFETIME_HOURS", 12),
            web_ui_max_content_length=_env_int("WEB_UI_MAX_CONTENT_LENGTH", 1_048_576),
            check_interval_seconds=_env_int("CHECK_INTERVAL_SECONDS", 300),
            max_emails_per_check=_env_int("MAX_EMAILS_PER_CHECK", 10),
            dry_run_target_events=_env_int("DRY_RUN_TARGET_EVENTS", 5),
            auto_approve_events=_env_bool("AUTO_APPROVE_EVENTS", False),
            enforce_internal_recipient_domain=_env_bool("ENFORCE_INTERNAL_RECIPIENT_DOMAIN", False),
            auto_approve_allow_external_recipients=_env_bool("AUTO_APPROVE_ALLOW_EXTERNAL_RECIPIENTS", False),
            dry_run=_env_bool("DRY_RUN", False),
            default_timezone=os.getenv("DEFAULT_TIMEZONE", "Europe/Berlin"),
            organizer_name=os.getenv("ORGANIZER_NAME", "iCal Bot"),
            invite_subject_prefix=os.getenv("INVITE_SUBJECT_PREFIX", ""),
            invite_subject_suffix=os.getenv("INVITE_SUBJECT_SUFFIX", " (iCal-Bot)"),
            web_ui_enabled=_env_bool("WEB_UI_ENABLED", True),
            web_ui_host=os.getenv("WEB_UI_HOST", "127.0.0.1"),
            web_ui_port=_env_int("WEB_UI_PORT", 5000),
        )

    @classmethod
    def from_config_module(cls, config_mod: Optional[Any] = None) -> "Settings":
        """Build a live ``Settings`` snapshot from the legacy ``config`` module.

        Reading through the module ensures test patches on ``config.*`` are
        respected uniformly across all new modules.

        Args:
            config_mod: Optional ``config`` module reference; imports ``config`` if omitted.

        Returns:
            Populated :class:`Settings` snapshot.
        """
        if config_mod is None:
            import config as config_mod

        lock = getattr(config_mod, "_CONFIG_LOCK", None)
        if lock is not None:
            lock.acquire()
        try:
            defaults = cls()
            kwargs = {}
            for field_name in defaults.__dataclass_fields__:
                attr = field_name.upper()
                kwargs[field_name] = getattr(config_mod, attr, getattr(defaults, field_name))
            return cls(**kwargs)
        finally:
            if lock is not None:
                lock.release()


__all__ = ["Settings"]
