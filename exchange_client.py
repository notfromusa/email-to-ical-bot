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
)
from exchangelib.items import SEND_TO_ALL_AND_SAVE_COPY, SEND_TO_NONE
from datetime import datetime, timedelta
import re
from email.utils import parseaddr
# from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter
from typing import Any, Dict, List, Optional, Set, Tuple
import logging

import config
from attachment_parser import AttachmentExtractor
from ical_bot.mail.common import (
    build_calendar_mime_message,
    extract_field_from_ics,
    extract_summary_from_ics,
    filter_allowed_recipients,
    resolve_invite_subject,
    sanitize_header_id,
    save_outbound_mime_dump,
    strip_reply_trail,
)
from ical_bot.time_utils import parse_iso_datetime
from identifiers import (
    fingerprint_body,
    generate_email_id,
    generate_stable_email_id,
    normalize_message_id,
    normalize_timestamp,
)
from messaging import build_calendar_description


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
        processed_ids: Set[str],
        max_emails: int = 10,
        *,
        offset: int = 0,
        skip_processed: bool = True,
        exclude_ids: Optional[Set[str]] = None,
        processed_message_ids: Optional[Set[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Fetch unprocessed emails from the Exchange inbox.

        Checks normalized ``Message-ID`` and header-derived stable email IDs
        before calling ``attachment_extractor.extract_from_exchange_item()`` so
        already-processed attachments are not downloaded or parsed redundantly.

        Args:
            processed_ids: Set of hashed email IDs already processed.
            max_emails: Maximum number of inbox items in this batch slice.
            offset: Batch offset (0 = newest batch).
            skip_processed: If True, skip items already seen by ID or Message-ID.
            exclude_ids: Additional IDs to ignore for the current run.
            processed_message_ids: Normalized RFC 5322 Message-IDs already processed.

        Returns:
            Tuple of ``(email list, has_more flag)``.
        """
        emails: List[Dict[str, Any]] = []
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
                timestamp = normalize_timestamp(item.datetime_received)
                date_str = timestamp or (item.datetime_received.isoformat() if item.datetime_received else "")
                item_subject = item.subject or "(No subject)"
                item_sender = item.sender.email_address if item.sender else ""
                norm_mid = normalize_message_id(item.message_id)

                if skip_processed and processed_message_ids and norm_mid and norm_mid in processed_message_ids:
                    continue

                stable_id = (
                    generate_stable_email_id(
                        message_id=item.message_id,
                        timestamp=date_str,
                        sender=item_sender,
                        subject=item_subject,
                    )
                    if norm_mid
                    else None
                )
                if stable_id is not None:
                    if exclude_ids and stable_id in exclude_ids:
                        continue
                    if skip_processed and stable_id in processed_ids:
                        continue

                # Extract body text & attachments only after header skip checks
                body = ""
                if item.text_body:
                    body = item.text_body
                elif item.body:
                    from html import unescape
                    body = unescape(re.sub('<[^<]+?>', '', str(item.body)))
                body = self._strip_reply_trail(body, subject=item.subject)
                attachment_text = self.attachment_extractor.extract_from_exchange_item(item)
                if attachment_text:
                    body = f"{body}\n\n{attachment_text}" if body else attachment_text

                recipients = self._extract_recipients(item)
                body_hash = fingerprint_body(body)
                legacy_id = generate_email_id(
                    message_id=item.message_id,
                    timestamp=date_str,
                    sender=item_sender,
                    subject=item_subject,
                    body_hash=body_hash,
                )
                resolved_id = stable_id if stable_id is not None else legacy_id

                if exclude_ids and (resolved_id in exclude_ids or legacy_id in exclude_ids):
                    continue
                if skip_processed and (resolved_id in processed_ids or legacy_id in processed_ids):
                    continue

                emails.append({
                    'id': resolved_id,
                    'legacy_id': legacy_id,
                    'source_id': item.message_id,
                    'message_id': item.message_id,
                    'subject': item_subject,
                    'sender': item_sender or "Unknown",
                    'date': date_str,
                    'body': body,
                    'original_msg': item,
                    'recipients': recipients,
                    'timestamp': date_str,
                })

                logger.info(f"Fetched email: {item.subject} from {item_sender or 'Unknown'}")

                if len(emails) >= max_emails:
                    break

        except Exception as e:
            logger.error(f"Error fetching emails: {e}")
            raise

        return emails, has_more

    def _strip_reply_trail(self, body: str, subject: Optional[str] = None) -> str:
        """Remove quoted previous messages and reply headers while preserving forwards."""
        return strip_reply_trail(body, subject=subject)

    @staticmethod
    def _extract_summary_from_ics(ics_content: str) -> Optional[str]:
        """Extract ``SUMMARY`` property from iCalendar (.ics) content if available."""
        return extract_summary_from_ics(ics_content)

    @staticmethod
    def _extract_field_from_ics(ics_content: str, field_name: str) -> Optional[str]:
        """Extract an unfolded and unescaped property value from iCalendar (.ics) content."""
        return extract_field_from_ics(ics_content, field_name)

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
            parsed = parse_iso_datetime(dt_val, default_tz=None)
            if parsed is None:
                raise ValueError(f"Cannot convert {dt_val} to EWSDateTime")
            dt_val = parsed
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

            subject_line = resolve_invite_subject(
                original_email,
                ics_content,
                event_data=event_data,
                event_title=event_title,
            )

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

                    # Compose rich calendar item body with meeting digest and disclaimer
                    if event_data:
                        calendar_body = build_calendar_description(event_data)
                    elif body_desc:
                        calendar_body = body_desc
                    else:
                        calendar_body = reply_message

                    if existing_id:
                        try:
                            existing_item = self.account.calendar.get(id=existing_id)
                            existing_item.subject = subject_line
                            existing_item.start = start_ews
                            existing_item.end = end_ews
                            existing_item.body = calendar_body
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
                            body=calendar_body,
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
                            clean_mid = sanitize_header_id(original_email.get('message_id'))
                            if clean_mid:
                                conf_msg.in_reply_to = clean_mid
                                conf_msg.references = clean_mid
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
            mime_msg = build_calendar_mime_message(
                sender=self.email_address,
                recipients=recipients,
                subject=subject_line,
                reply_message=reply_message,
                ics_content=ics_content,
                in_reply_to=original_email.get('message_id'),
            )

            reply = Message(
                account=self.account,
                to_recipients=mailbox_recipients,
            )
            reply.mime_content = mime_msg.as_bytes()

            save_outbound_mime_dump(reply.mime_content, prefix="outbound_invite_ews")

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
        """Validate recipient email addresses and optionally enforce internal domain policy."""
        return filter_allowed_recipients(recipients, self_email=getattr(self, 'email_address', ''))

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
            body = self._strip_reply_trail(body, subject=item.subject)
            attachment_text = self.attachment_extractor.extract_from_exchange_item(item)
            if attachment_text:
                body = f"{body}\n\n{attachment_text}" if body else attachment_text

            recipients = self._extract_recipients(item)
            timestamp = normalize_timestamp(item.datetime_received)
            date_str = timestamp or (item.datetime_received.isoformat() if item.datetime_received else "")
            item_subject = item.subject or "(No subject)"
            item_sender = item.sender.email_address if item.sender else ""
            body_hash = fingerprint_body(body)
            legacy_id = generate_email_id(
                message_id=item.message_id,
                timestamp=date_str,
                sender=item_sender,
                subject=item_subject,
                body_hash=body_hash,
            )
            resolved_id = (
                generate_stable_email_id(
                    message_id=item.message_id,
                    timestamp=date_str,
                    sender=item_sender,
                    subject=item_subject,
                    body_hash=body_hash,
                )
                if normalize_message_id(item.message_id)
                else legacy_id
            )
            return {
                'id': resolved_id,
                'legacy_id': legacy_id,
                'source_id': item.message_id,
                'message_id': item.message_id,
                'subject': item_subject,
                'sender': item_sender or "Unknown",
                'date': date_str,
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
                clean_target_mid = normalize_message_id(message_id)
                fetched_mid = normalize_message_id(data.get('message_id')) if data else ''
                if data and (
                    data.get('id') == email_id
                    or data.get('legacy_id') == email_id
                    or not email_id
                    or (clean_target_mid and fetched_mid == clean_target_mid)
                ):
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
                    body = unescape(re.sub('<[^<]+?>', '', str(item.body)))
                body = self._strip_reply_trail(body, subject=item.subject)
                attachment_text = self.attachment_extractor.extract_from_exchange_item(item)
                if attachment_text:
                    body = f"{body}\n\n{attachment_text}" if body else attachment_text

                recipients = self._extract_recipients(item)
                timestamp = normalize_timestamp(item.datetime_received)
                date_str = timestamp or (item.datetime_received.isoformat() if item.datetime_received else "")
                item_subject = item.subject or "(No subject)"
                item_sender = item.sender.email_address if item.sender else ""
                body_hash = fingerprint_body(body)
                legacy_id = generate_email_id(
                    message_id=item.message_id,
                    timestamp=date_str,
                    sender=item_sender,
                    subject=item_subject,
                    body_hash=body_hash,
                )
                resolved_id = (
                    generate_stable_email_id(
                        message_id=item.message_id,
                        timestamp=date_str,
                        sender=item_sender,
                        subject=item_subject,
                        body_hash=body_hash,
                    )
                    if normalize_message_id(item.message_id)
                    else legacy_id
                )

                email_dict = {
                    'id': resolved_id,
                    'legacy_id': legacy_id,
                    'source_id': item.message_id,
                    'message_id': item.message_id,
                    'subject': item_subject,
                    'sender': item_sender or "Unknown",
                    'date': date_str,
                    'body': body,
                    'original_msg': item,
                    'recipients': recipients,
                }

                if resolved_id == email_id or legacy_id == email_id:
                    return email_dict

                # Secondary match if message_id matches
                if message_id and item.message_id:
                    c1 = normalize_message_id(message_id)
                    c2 = normalize_message_id(item.message_id)
                    if c1 and c1 == c2 and not fallback_match:
                        fallback_match = email_dict

                # Tertiary match if subject and sender match
                if subject and sender and not fallback_match:
                    if (item.subject or '').strip() == subject.strip() and item_sender.lower() == sender.lower():
                        fallback_match = email_dict

            return fallback_match
        except Exception as exc:
            logger.error("Error scanning Exchange inbox for email %s: %s", email_id, exc)
            return None
