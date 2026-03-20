"""
Exchange email client for reading and sending emails via Exchange Web Services (EWS)
"""

from exchangelib import (
    Credentials,
    Account,
    Configuration,
    DELEGATE,
    Message,
    Mailbox,
    Version,
    Build,
)
from pathlib import Path
from datetime import datetime
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.utils import parseaddr
# from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter
from typing import List, Dict, Optional, Tuple
import logging

import config
from attachment_parser import AttachmentExtractor
from identifiers import generate_email_id, fingerprint_body, normalize_timestamp
from messaging import build_reply_message_html_from_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Disable SSL verification warnings if needed (not recommended for production)
# BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter


class ExchangeClient:
    def __init__(self):
        self.email_address = config.EMAIL_ADDRESS
        self.email_password = config.EMAIL_PASSWORD
        self.account = None
        self.attachment_extractor = AttachmentExtractor()
        self._connect()
        
    def _connect(self):
        """Connect to Exchange server"""
        # Authentication can differ from mailbox address (e.g., ac-number vs email)
        username = (
            getattr(config, 'EXCHANGE_USERNAME', '')
            or getattr(config, 'EXCHANGE_EMAIL', '')
            or self.email_address
        )

        credentials = Credentials(username=username, password=self.email_password)
        autodiscover_error = None

        if config.EXCHANGE_AUTODISCOVER:
            try:
                logger.info(f"Using autodiscover for {self.email_address}")
                self.account = Account(
                    primary_smtp_address=self.email_address,
                    credentials=credentials,
                    autodiscover=True,
                    access_type=DELEGATE,
                )
            except Exception as exc:
                autodiscover_error = exc
                logger.warning(
                    "Autodiscover failed (%s). Falling back to manual server settings...",
                    exc,
                )

        if self.account is None:
            self.account = self._connect_manual(credentials)

        # Test connection
        _ = self.account.inbox.total_count
        logger.info("Successfully connected to Exchange")

        if autodiscover_error:
            logger.info("Manual configuration succeeded after autodiscover failure")

    def _connect_manual(self, credentials):
        """Fallback manual configuration when autodiscover is unavailable"""
        server = getattr(config, 'EXCHANGE_SERVER', None)
        if not server:
            raise ValueError("EXCHANGE_SERVER must be set when autodiscover is disabled")

        logger.info(f"Connecting to {server} (manual configuration)")

        # Map friendly version names to exchangelib Version objects
        version_name = getattr(config, 'EXCHANGE_VERSION', 'Exchange2016')
        version_map = {
            'Exchange2013': Version(build=Build(15, 0, 847, 32)),
            'Exchange2016': Version(build=Build(15, 1, 845, 34)),
            'Exchange2019': Version(build=Build(15, 2, 858, 5)),
            # Office365 is auto-detected best by exchangelib when version is omitted
        }
        version_obj = version_map.get(version_name)

        config_kwargs = {
            'server': server,
            'credentials': credentials,
        }
        if version_obj:
            config_kwargs['version'] = version_obj

        server_config = Configuration(**config_kwargs)

        return Account(
            primary_smtp_address=self.email_address,
            config=server_config,
            autodiscover=False,
            access_type=DELEGATE,
        )
    
    def fetch_unprocessed_emails(
        self,
        processed_ids: set,
        max_emails: int = 10,
        *,
        offset: int = 0,
        skip_processed: bool = True,
        exclude_ids: Optional[set] = None
    ) -> Tuple[List[Dict], bool]:
        """
        Fetch unprocessed emails from inbox
        
        Args:
            processed_ids: Set of email IDs that have already been processed
            max_emails: Maximum number of emails to fetch
            offset: Batch offset (0 = newest batch)
            skip_processed: If True, exclude already processed IDs
            exclude_ids: Additional IDs to exclude this run
            
        Returns:
            Tuple of (email list, has_more flag)
        """
        emails = []
        has_more = False
        
        try:
            start = offset * max_emails
            end = start + max_emails + 1
            queryset = self.account.inbox.all().order_by('-datetime_received')
            items = list(queryset[start:end])
            if len(items) > max_emails:
                has_more = True
                items = items[:max_emails]
            
            for item in items:
                # Extract body text
                body = ""
                if item.text_body:
                    body = item.text_body
                elif item.body:
                    # If no text body, use HTML body (stripped)
                    from html import unescape
                    import re
                    body = unescape(re.sub('<[^<]+?>', '', str(item.body)))
                body = self._strip_reply_trail(body)
                attachment_text = self.attachment_extractor.extract_from_exchange_item(item)
                if attachment_text:
                    body = f"{body}\n\n{attachment_text}" if body else attachment_text
                
                recipients = self._extract_recipients(item)

                timestamp = normalize_timestamp(item.datetime_received)
                body_hash = fingerprint_body(body)
                hashed_id = generate_email_id(
                    message_id=item.message_id,
                    timestamp=timestamp or (item.datetime_received.isoformat() if item.datetime_received else ""),
                    sender=item.sender.email_address if item.sender else "",
                    subject=item.subject or "(No subject)",
                    body_hash=body_hash,
                )

                if exclude_ids and hashed_id in exclude_ids:
                    continue
                if skip_processed and hashed_id in processed_ids:
                    continue

                emails.append({
                    'id': hashed_id,
                    'source_id': item.message_id,
                    'message_id': item.message_id,
                    'subject': item.subject or "(No subject)",
                    'sender': item.sender.email_address if item.sender else "Unknown",
                    'date': timestamp or (item.datetime_received.isoformat() if item.datetime_received else ""),
                    'body': body,
                    'original_msg': item,
                    'recipients': recipients,
                    'timestamp': timestamp or (item.datetime_received.isoformat() if item.datetime_received else ""),
                })
                
                logger.info(f"Fetched email: {item.subject} from {item.sender.email_address if item.sender else 'Unknown'}")
                
                if len(emails) >= max_emails:
                    break
            
        except Exception as e:
            logger.error(f"Error fetching emails: {e}")
            raise
        
        return emails, has_more

    def _strip_reply_trail(self, body: str) -> str:
        """Remove quoted previous messages and reply headers for cleaner LLM input."""
        if not body:
            return body
        markers = [
            '-----original message-----',
            '----- reply message -----',
            '-----ursprüngliche nachricht-----',
        ]
        header_block_prefixes = ('from:', 'de:', 'gesendet:', 'sent:', 'to:', 'an:', 'subject:', 'betreff:', 'cc:')
        reply_header_patterns = [
            re.compile(r'^on\s.+wrote:\s*$', re.IGNORECASE),
            re.compile(r'^am\s.+schrieb.+:\s*$', re.IGNORECASE),
        ]
        lines = body.splitlines()
        for idx, line in enumerate(lines):
            trimmed = line.strip().lower()
            if not trimmed:
                continue
            if trimmed.startswith('>'):
                return '\n'.join(lines[:idx]).strip()
            for marker in markers:
                if trimmed.startswith(marker):
                    return '\n'.join(lines[:idx]).strip()
            if any(pattern.match(trimmed) for pattern in reply_header_patterns):
                return '\n'.join(lines[:idx]).strip()
            if trimmed.startswith(header_block_prefixes):
                next_lines = [
                    (lines[idx + offset].strip().lower() if idx + offset < len(lines) else '')
                    for offset in (1, 2, 3)
                ]
                if any(next_line.startswith(header_block_prefixes) for next_line in next_lines if next_line):
                    return '\n'.join(lines[:idx]).strip()
        return body.strip()
    
    def send_reply_with_calendar(self, original_email: Dict, ics_content: str, 
                                 reply_message: str = "I've created a calendar invite for this event.",
                                 dry_run: bool = False):
        """
        Send a reply to an email with calendar invite attached
        
        Args:
            original_email: Dictionary containing original email data
            ics_content: iCalendar file content as string
            reply_message: Message body for the reply
            dry_run: If True, only preview without sending
        """
        try:
            # Get the original message for threading and richer metadata
            original_msg = original_email.get('original_msg')
            if not original_msg and original_email.get('message_id'):
                original_msg = self._find_message_by_id(original_email['message_id'])

            recipients = original_email.get('recipients') or []
            if not recipients and original_msg:
                recipients = self._extract_recipients(original_msg)
            if not recipients:
                fallback = parseaddr(original_email.get('sender', ''))[1]
                if fallback:
                    recipients = [fallback]

            recipients = self._filter_allowed_recipients(recipients)
            if not recipients:
                message = "No allowable recipients found; aborting send"
                logger.warning(message)
                raise RuntimeError(message)

            mailbox_recipients = [Mailbox(email_address=addr) for addr in recipients]

            result = {
                'recipients': recipients,
                'dry_run': bool(dry_run),
                'protocol': 'ews',
                'status': 'pending',
                'message_id': original_email.get('message_id'),
            }

            if dry_run:
                logger.info("=" * 60)
                logger.info("DRY RUN MODE - Email NOT sent")
                logger.info("To: %s", ', '.join(recipients))
                logger.info("Subject: Re: %s", original_email.get('subject'))
                logger.info("Message length: %s chars", len(reply_message))
                logger.info("Calendar invite attached (size: %s bytes)", len(ics_content))
                logger.info("=" * 60)
                result['status'] = 'dry_run_preview'
                return result

            # Build a MIME message with inline calendar so Outlook shows buttons
            mime_msg = MIMEMultipart('mixed')
            mime_msg['From'] = self.email_address
            mime_msg['To'] = ', '.join(recipients)
            mime_msg['Subject'] = f"Re: {original_email['subject']}"

            if original_email.get('message_id'):
                mime_msg['In-Reply-To'] = original_email['message_id']
                mime_msg['References'] = original_email['message_id']

            alt = MIMEMultipart('alternative')
            mime_msg.attach(alt)

            alt.attach(MIMEText(reply_message, 'plain', 'utf-8'))
            alt.attach(MIMEText(build_reply_message_html_from_text(reply_message), 'html', 'utf-8'))

            calendar_part = MIMEText(ics_content, 'calendar', 'utf-8')
            calendar_part.replace_header('Content-Type', 'text/calendar; method=REQUEST; charset="UTF-8"')
            calendar_part.add_header('Content-Disposition', 'inline')
            calendar_part.add_header('Content-Class', 'urn:content-classes:calendarmessage')
            alt.attach(calendar_part)

            ics_attachment = MIMEBase('text', 'calendar', method='REQUEST', name='invite.ics')
            ics_attachment.set_payload(ics_content)
            encoders.encode_base64(ics_attachment)
            ics_attachment.add_header('Content-Disposition', 'attachment', filename='invite.ics')
            mime_msg.attach(ics_attachment)

            reply = Message(
                account=self.account,
                to_recipients=mailbox_recipients,
            )
            reply.mime_content = mime_msg.as_bytes()

            # Persist MIME for debugging (opt-in)
            if getattr(config, 'ENABLE_DEBUG_DUMPS', False):
                try:
                    dump_dir = Path(getattr(config, 'RESULTS_DB_PATH', '.') ).parent
                    dump_path = dump_dir / f"outbound_invite_ews_{datetime.utcnow().strftime('%Y%m%d_%H%M%S%f')}.eml"
                    dump_path.write_bytes(reply.mime_content)
                    logger.info("Saved outbound EWS MIME to %s", dump_path)
                except Exception as exc:
                    logger.warning("Failed to save outbound EWS MIME: %s", exc)

            reply.send()
            result['status'] = 'sent'
            result['message_id'] = getattr(reply, 'message_id', result['message_id'])
            
            logger.info("Reply sent successfully to %s", ', '.join(recipients))
            return result
            
        except Exception as e:
            logger.error(f"Error sending reply: {e}")
            raise

    def _find_message_by_id(self, message_id: Optional[str]):
        if not message_id:
            return None
        try:
            return self.account.inbox.filter(message_id=message_id).first()
        except Exception as exc:
            logger.warning("Unable to locate original message for %s: %s", message_id, exc)
            return None

    def get_message_by_id(self, message_id: Optional[str]):
        """Public helper for retrieving an Exchange message by its message-id."""
        return self._find_message_by_id(message_id)

    def _mailbox_to_email(self, mailbox) -> str:
        if not mailbox:
            return ""
        if isinstance(mailbox, Mailbox):
            return (mailbox.email_address or "").strip()
        if isinstance(mailbox, str):
            return parseaddr(mailbox)[1]
        return parseaddr(str(mailbox))[1]

    def _extract_recipients(self, message) -> List[str]:
        recipients: List[str] = []

        def add_mailbox(mailbox_obj):
            email_addr = self._mailbox_to_email(mailbox_obj)
            if not email_addr:
                return
            if email_addr not in recipients:
                recipients.append(email_addr)

        if not message:
            return recipients

        for mailbox in (message.reply_to or []):
            add_mailbox(mailbox)
        for mailbox in (message.to_recipients or []):
            add_mailbox(mailbox)
        for mailbox in (message.cc_recipients or []):
            add_mailbox(mailbox)

        # Include sender so they stay in the loop
        add_mailbox(message.sender)

        return recipients

    def _filter_allowed_recipients(self, recipients: List[str]) -> List[str]:
        if not recipients:
            return []
        allowed = []
        for addr in recipients:
            parsed = parseaddr(addr)[1]
            if not parsed:
                continue
            if parsed not in allowed:
                allowed.append(parsed)
        return allowed
