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
    EWSTimeZone,
    EWSDateTime,
    CalendarItem,
    Attendee,
)
from exchangelib.items import SEND_TO_ALL_AND_SAVE_COPY, SEND_TO_NONE
from pathlib import Path
from datetime import datetime, timezone, timedelta
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
from messaging import build_reply_message_html_from_text, format_invite_subject


class SuppressNaiveDatetimeFilter(logging.Filter):
    """Filter out exchangelib's repetitive naive datetime INFO log messages.

    Exchange Web Services (EWS) frequently returns item timestamps (such as last_modified_time)
    without an explicit timezone offset, prompting exchangelib to assume the account's default
    timezone and log an INFO notice for each occurrence.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter records containing naive datetime timezone assumption notices.

        Args:
            record: The logging record to evaluate.

        Returns:
            bool: False if the log record announces a naive datetime assumption, True otherwise.
        """
        return "Found naive datetime" not in record.getMessage()


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Suppress repetitive naive datetime noise while allowing other exchangelib INFO logs through
logging.getLogger("exchangelib.fields").addFilter(SuppressNaiveDatetimeFilter())

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
        default_tz = self._get_default_timezone()

        if config.EXCHANGE_AUTODISCOVER:
            try:
                logger.info(f"Using autodiscover for {self.email_address}")
                self.account = Account(
                    primary_smtp_address=self.email_address,
                    credentials=credentials,
                    autodiscover=True,
                    access_type=DELEGATE,
                    default_timezone=default_tz,
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

    def _get_default_timezone(self) -> EWSTimeZone:
        """Resolve the configured default timezone for the Exchange account.

        Returns:
            EWSTimeZone: An EWSTimeZone instance matching config.DEFAULT_TIMEZONE,
                falling back to the local host timezone if invalid.
        """
        tz_name = getattr(config, 'DEFAULT_TIMEZONE', 'Europe/Berlin')
        try:
            return EWSTimeZone(tz_name)
        except Exception as exc:
            logger.warning(
                "Failed to parse timezone '%s' (%s). Falling back to local timezone.",
                tz_name,
                exc,
            )
            return EWSTimeZone.localzone()

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
        default_tz = self._get_default_timezone()

        return Account(
            primary_smtp_address=self.email_address,
            config=server_config,
            autodiscover=False,
            access_type=DELEGATE,
            default_timezone=default_tz,
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
    
    @staticmethod
    def _extract_summary_from_ics(ics_content: str) -> Optional[str]:
        """Extract SUMMARY property from iCalendar (.ics) content if available.

        Args:
            ics_content: Raw iCalendar file content.

        Returns:
            Extracted and unescaped SUMMARY string, or None if not found.
        """
        return ExchangeClient._extract_field_from_ics(ics_content, "SUMMARY")

    @staticmethod
    def _extract_field_from_ics(ics_content: str, field_name: str) -> Optional[str]:
        """Extract a property value from iCalendar (.ics) content unfolded and unescaped.

        Per RFC 5545 Section 3.1, long lines are folded with CRLF followed by space/tab.
        Text values escape commas, semicolons, backslashes, and newlines (RFC 5545 Section 3.3.11).

        Args:
            ics_content: Raw iCalendar file content string.
            field_name: The iCalendar property name to extract (e.g. DTSTART, SUMMARY, LOCATION).

        Returns:
            Extracted and unescaped property string, or None if not found.
        """
        if not ics_content or not field_name:
            return None
        pattern = rf"(?:^|\r?\n){field_name}(?:;[^:\r\n]*)?:([^\r\n]*(?:\r?\n[\t ][^\r\n]*)*)"
        match = re.search(pattern, ics_content, re.IGNORECASE)
        if not match:
            return None
        val = re.sub(r"\r?\n[\t ]", "", match.group(1))
        return (
            val.replace(r"\,", ",")
            .replace(r"\;", ";")
            .replace("\\\\", "\\")
            .replace(r"\n", "\n")
            .replace(r"\N", "\n")
            .strip()
            or None
        )

    def _to_ews_datetime(self, dt_val, default_tz: EWSTimeZone) -> EWSDateTime:
        """Convert a datetime string or object into a timezone-aware EWSDateTime.

        Args:
            dt_val: datetime object, EWSDateTime, or ISO-8601 / iCalendar date string.
            default_tz: The fallback EWSTimeZone to localize naive timestamps.

        Returns:
            EWSDateTime: Timezone-aware EWSDateTime localized in default_tz.
        """
        if isinstance(dt_val, EWSDateTime):
            return dt_val.astimezone(default_tz)
        if isinstance(dt_val, str):
            normalized = dt_val.strip().replace('Z', '+00:00')
            if 'T' not in normalized and ' ' in normalized:
                normalized = normalized.replace(' ', 'T', 1)
            if len(normalized) == 16 and normalized[8] == 'T' and normalized.endswith('+00:00'):
                dt_val = datetime.strptime(normalized, '%Y%m%dT%H%M%S+00:00').replace(tzinfo=timezone.utc)
            elif len(normalized) == 15 and normalized[8] == 'T':
                dt_val = datetime.strptime(normalized, '%Y%m%dT%H%M%S')
            else:
                dt_val = datetime.fromisoformat(normalized)
        if isinstance(dt_val, datetime):
            if dt_val.tzinfo is None:
                return default_tz.from_date(dt_val) if hasattr(default_tz, 'from_date') else EWSDateTime(
                    dt_val.year, dt_val.month, dt_val.day, dt_val.hour, dt_val.minute, dt_val.second, tzinfo=default_tz
                )
            res = dt_val.astimezone(default_tz)
            if isinstance(res, EWSDateTime):
                return res
            return EWSDateTime.from_datetime(res)
        raise ValueError(f"Cannot convert {dt_val} to EWSDateTime")

    def send_reply_with_calendar(
        self,
        original_email: Dict,
        ics_content: str, 
        reply_message: str = "I've created a calendar invite for this event.",
        dry_run: bool = False,
        event_data: Optional[Dict] = None,
        event_title: Optional[str] = None,
    ):
        """Send a calendar invite via Exchange (EWS).

        When EXCHANGE_DIRECT_CALENDAR_CREATION is enabled (Option A), creates or updates
        the event directly in the user's primary Exchange calendar folder (account.calendar)
        via EWS CalendarItem.save(). This ensures the user is the owner/organizer of the event
        and can edit, reschedule, or cancel it natively in Outlook (RFC 5546 Section 1.4).

        When disabled, falls back to sending a MIME message with an inline text/calendar part.
        In accordance with RFC 6047 Section 2.3, the MIME Subject header field is derived from
        the specific event title rather than the parent email subject.

        Args:
            original_email: Dictionary containing original email data.
            ics_content: iCalendar file content as string.
            reply_message: Message body for the reply.
            dry_run: If True, only preview without sending.
            event_data: Optional dictionary containing event details (title, start_datetime, etc.).
            event_title: Optional explicit title/short description of the event.

        Returns:
            Dictionary containing send result status, recipients, protocol, and subject.
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

            # Determine appropriate event title for the subject line
            resolved_title = (
                (event_title or '').strip()
                or ((event_data.get('title') if event_data else '') or '').strip()
                or (original_email.get('event_title') or '').strip()
            )
            if not resolved_title and ics_content:
                resolved_title = self._extract_summary_from_ics(ics_content)

            subject_line = format_invite_subject(resolved_title, original_email.get('subject'))

            # Check if direct calendar creation is enabled (Option A)
            direct_calendar = getattr(config, 'EXCHANGE_DIRECT_CALENDAR_CREATION', True)
            if direct_calendar:
                start_val = (event_data.get('start_datetime') if event_data else None)
                end_val = (event_data.get('end_datetime') if event_data else None)
                location = (event_data.get('location') if event_data else None)
                body_desc = (event_data.get('description') if event_data else None)

                if not start_val and ics_content:
                    start_val = self._extract_field_from_ics(ics_content, 'DTSTART')
                if not end_val and ics_content:
                    end_val = self._extract_field_from_ics(ics_content, 'DTEND')
                if not location and ics_content:
                    location = self._extract_field_from_ics(ics_content, 'LOCATION')
                if not body_desc and ics_content:
                    body_desc = self._extract_field_from_ics(ics_content, 'DESCRIPTION')

                default_tz = self._get_default_timezone()
                start_ews = None
                end_ews = None
                if start_val:
                    try:
                        start_ews = self._to_ews_datetime(start_val, default_tz)
                        if end_val:
                            end_ews = self._to_ews_datetime(end_val, default_tz)
                        if not end_ews or end_ews <= start_ews:
                            end_ews = start_ews + timedelta(hours=1)
                    except Exception as exc:
                        logger.warning("Could not convert start/end to EWSDateTime (%s); fallback to MIME email", exc)

                category = getattr(config, 'CALENDAR_ITEM_CATEGORY', 'iCal-Bot')
                categories = [category] if category else None

                result = {
                    'recipients': recipients,
                    'dry_run': bool(dry_run),
                    'protocol': 'ews',
                    'status': 'pending',
                    'message_id': original_email.get('message_id'),
                    'subject': subject_line,
                    'direct_calendar_item': True,
                }

                if dry_run:
                    logger.info("=" * 60)
                    logger.info("DRY RUN MODE - CalendarItem NOT saved to Exchange calendar")
                    logger.info("To: %s", ', '.join(recipients))
                    logger.info("Subject: %s", subject_line)
                    logger.info("Start: %s, End: %s", start_ews, end_ews)
                    logger.info("Location: %s", location or "(none)")
                    logger.info("Categories: %s", categories or [])
                    logger.info("Direct calendar mode enabled: user is organizer, editable natively in Outlook")
                    logger.info("=" * 60)
                    result['status'] = 'dry_run_preview'
                    return result

                # Only proceed with direct calendar saving if valid start time was resolved
                if start_ews:
                    if self.account is None:
                        self._connect()

                    external_recipients = [r for r in recipients if r.lower() != (self.email_address or '').lower()]
                    invite_attendees = getattr(config, 'EXCHANGE_CALENDAR_INVITE_ATTENDEES', False)

                    calendar_item = None
                    existing_id = original_email.get('calendar_item_id')
                    if existing_id:
                        try:
                            existing_item = self.account.calendar.get(id=existing_id)
                            existing_item.subject = subject_line
                            existing_item.start = start_ews
                            existing_item.end = end_ews
                            existing_item.body = body_desc or reply_message
                            existing_item.location = location or None
                            if categories:
                                existing_item.categories = categories
                            update_fields = ['subject', 'start', 'end', 'body', 'location']
                            if categories:
                                update_fields.append('categories')
                            if external_recipients and invite_attendees:
                                existing_item.required_attendees = external_recipients
                                update_fields.append('required_attendees')
                                existing_item.save(update_fields=update_fields, send_meeting_invitations=SEND_TO_ALL_AND_SAVE_COPY)
                            else:
                                existing_item.save(update_fields=update_fields, send_meeting_invitations=SEND_TO_NONE)
                            calendar_item = existing_item
                            logger.info("Updated existing CalendarItem %s in calendar: '%s'", calendar_item.id, subject_line)
                        except Exception as upd_err:
                            logger.warning("Could not update existing CalendarItem %s: %s; creating new one", existing_id, upd_err)

                    if not calendar_item:
                        new_item = CalendarItem(
                            folder=self.account.calendar,
                            subject=subject_line,
                            body=body_desc or reply_message,
                            start=start_ews,
                            end=end_ews,
                            location=location or None,
                            categories=categories,
                        )
                        if external_recipients and invite_attendees:
                            new_item.required_attendees = external_recipients
                            new_item.save(send_meeting_invitations=SEND_TO_ALL_AND_SAVE_COPY)
                        else:
                            new_item.save(send_meeting_invitations=SEND_TO_NONE)
                        calendar_item = new_item
                        logger.info("Created new CalendarItem %s in Exchange calendar: '%s'", calendar_item.id, subject_line)

                    result['status'] = 'sent'
                    result['calendar_item_id'] = getattr(calendar_item, 'id', None)

                    # Optional confirmation email to thread
                    if getattr(config, 'EXCHANGE_SEND_CONFIRMATION_EMAIL', False):
                        try:
                            mailbox_recipients = [Mailbox(email_address=addr) for addr in recipients]
                            conf_msg = Message(
                                account=self.account,
                                to_recipients=mailbox_recipients,
                                subject=f"Added to Calendar: {subject_line}",
                                body=reply_message,
                            )
                            if original_email.get('message_id'):
                                conf_msg.in_reply_to = original_email['message_id']
                                conf_msg.references = original_email['message_id']
                            conf_msg.send()
                            result['message_id'] = getattr(conf_msg, 'message_id', result['message_id'])
                            logger.info("Confirmation email sent to %s", ', '.join(recipients))
                        except Exception as conf_err:
                            logger.warning("Failed to send confirmation email: %s", conf_err)

                    return result

            # Fallback legacy MIME email sending path
            mailbox_recipients = [Mailbox(email_address=addr) for addr in recipients]

            result = {
                'recipients': recipients,
                'dry_run': bool(dry_run),
                'protocol': 'ews',
                'status': 'pending',
                'message_id': original_email.get('message_id'),
                'subject': subject_line,
            }

            if dry_run:
                logger.info("=" * 60)
                logger.info("DRY RUN MODE - Email NOT sent")
                logger.info("To: %s", ', '.join(recipients))
                logger.info("Subject: %s", subject_line)
                logger.info("Message length: %s chars", len(reply_message))
                logger.info("Calendar invite attached (size: %s bytes)", len(ics_content))
                logger.info("=" * 60)
                result['status'] = 'dry_run_preview'
                return result

            # Build a MIME message with inline calendar so Outlook shows buttons
            mime_msg = MIMEMultipart('mixed')
            mime_msg['From'] = self.email_address
            mime_msg['To'] = ', '.join(recipients)
            mime_msg['Subject'] = subject_line

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
        """Locate an Exchange message item by its Message-ID header."""
        if not message_id:
            return None
        clean_id = message_id.strip()
        candidates = [clean_id]
        if clean_id.startswith('<') and clean_id.endswith('>'):
            candidates.append(clean_id[1:-1].strip())
        else:
            candidates.append(f"<{clean_id}>")

        for cand in candidates:
            try:
                item = self.account.inbox.filter(message_id=cand).first()
                if item:
                    return item
            except Exception as exc:
                logger.debug("Exchange filter query for message_id %s failed: %s", cand, exc)
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

    def fetch_email_by_message_id(self, message_id: str) -> Optional[Dict]:
        """
        Fetch a single email by its Message-ID from Exchange inbox.

        Args:
            message_id: The Message-ID header to look up.

        Returns:
            Dict containing parsed email metadata and body, or None if not found.
        """
        if not message_id:
            return None
        try:
            item = self.get_message_by_id(message_id)
            if not item:
                return None
            body = ""
            if item.text_body:
                body = item.text_body
            elif item.body:
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
            return {
                'id': hashed_id,
                'source_id': item.message_id,
                'message_id': item.message_id,
                'subject': item.subject or "(No subject)",
                'sender': item.sender.email_address if item.sender else "Unknown",
                'date': timestamp or (item.datetime_received.isoformat() if item.datetime_received else ""),
                'body': body,
                'original_msg': item,
                'recipients': recipients,
            }
        except Exception as e:
            logger.error("Error fetching Exchange email by Message-ID %s: %s", message_id, e)
            return None

    def fetch_email_by_id(
        self,
        email_id: str,
        message_id: Optional[str] = None,
        subject: Optional[str] = None,
        sender: Optional[str] = None,
        max_search: int = 100,
    ) -> Optional[Dict]:
        """
        Fetch an email from Exchange by its deterministic hash ID or message ID.

        First attempts direct lookup by Message-ID (if available). If that fails
        or produces no match, scans recent inbox messages from the Exchange server,
        computing each email's deterministic hash ID via `generate_email_id()` to find
        the identical message that produced `email_id`.

        Args:
            email_id: The deterministic hash identifier (msg_...) of the email.
            message_id: Optional RFC 2822 Message-ID header.
            subject: Optional subject for secondary fallback matching.
            sender: Optional sender for secondary fallback matching.
            max_search: Maximum number of recent inbox messages to inspect.

        Returns:
            Dict containing parsed email metadata and body, or None if not found.
        """
        # 1. Try fast lookup by message_id if available
        if message_id:
            try:
                data = self.fetch_email_by_message_id(message_id)
                if data and (data.get('id') == email_id or not email_id):
                    return data
            except Exception as exc:
                logger.warning("Direct message_id fetch failed during fetch_email_by_id: %s", exc)

        # 2. Scan recent emails in inbox to match by deterministic hash
        try:
            queryset = self.account.inbox.all().order_by('-datetime_received')
            items = list(queryset[:max_search])
            fallback_match = None

            for item in items:
                body = ""
                if item.text_body:
                    body = item.text_body
                elif item.body:
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

                email_dict = {
                    'id': hashed_id,
                    'source_id': item.message_id,
                    'message_id': item.message_id,
                    'subject': item.subject or "(No subject)",
                    'sender': item.sender.email_address if item.sender else "Unknown",
                    'date': timestamp or (item.datetime_received.isoformat() if item.datetime_received else ""),
                    'body': body,
                    'original_msg': item,
                    'recipients': recipients,
                }

                if hashed_id == email_id:
                    return email_dict

                # Secondary match if message_id matches
                if message_id and item.message_id:
                    c1 = (message_id or '').strip().strip('<>')
                    c2 = (item.message_id or '').strip().strip('<>')
                    if c1 and c1 == c2 and not fallback_match:
                        fallback_match = email_dict

                # Tertiary match if subject and sender match
                if subject and sender and not fallback_match:
                    item_sender = item.sender.email_address if item.sender else ""
                    if (item.subject or '').strip() == subject.strip() and item_sender.lower() == sender.lower():
                        fallback_match = email_dict

            return fallback_match
        except Exception as exc:
            logger.error("Error scanning Exchange inbox for email %s: %s", email_id, exc)
            return None
