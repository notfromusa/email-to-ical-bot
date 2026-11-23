"""Formal MailAdapter protocol seam and factory for IMAP/SMTP and Exchange backends."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Set, Tuple, runtime_checkable

import config


@runtime_checkable
class MailAdapter(Protocol):
    """Protocol defining the interface satisfied by concrete mailbox adapters."""

    email_address: str

    def fetch_unprocessed_emails(
        self,
        processed_ids: Set[str],
        max_emails: int = 10,
        *,
        offset: int = 0,
        skip_processed: bool = True,
        exclude_ids: Optional[Set[str]] = None,
        processed_message_ids: Optional[Set[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Fetch a batch of emails from the inbox."""

    def fetch_email_by_message_id(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single email by its RFC 5322 Message-ID header."""

    def fetch_email_by_id(
        self,
        email_id: str,
        message_id: Optional[str] = None,
        subject: Optional[str] = None,
        sender: Optional[str] = None,
        max_search: int = 100,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a single email matching its deterministic hashed identifier."""

    def send_reply_with_calendar(
        self,
        original_email: Dict[str, Any],
        ics_content: str,
        reply_message: str = "I've created a calendar invite for this event.",
        dry_run: bool = False,
        event_data: Optional[Dict[str, Any]] = None,
        event_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a calendar invite reply or create a native calendar item."""


def create_mail_adapter(protocol: Optional[str] = None) -> MailAdapter:
    """Instantiate the configured mail adapter (Exchange EWS or IMAP/SMTP).

    Args:
        protocol: Optional explicit protocol override (``'EXCHANGE'`` or ``'IMAP'``).

    Returns:
        Concrete :class:`MailAdapter` instance.
    """
    selected = (protocol or getattr(config, "EMAIL_PROTOCOL", "IMAP") or "IMAP").strip().upper()
    if selected == "EXCHANGE":
        from exchange_client import ExchangeClient

        return ExchangeClient()
    from email_client import EmailClient

    return EmailClient()


__all__ = ["MailAdapter", "create_mail_adapter"]
