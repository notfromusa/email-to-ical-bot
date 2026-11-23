"""
Email client for reading and sending emails
"""

import imaplib
import smtplib
import ssl
import email
from email.utils import parseaddr, getaddresses
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import logging

import config
from attachment_parser import AttachmentExtractor
from ical_bot.mail.common import (
    build_calendar_mime_message,
    extract_summary_from_ics,
    filter_allowed_recipients,
    resolve_invite_subject,
    save_outbound_mime_dump,
    strip_reply_trail,
)
from identifiers import (
    fingerprint_body,
    generate_email_id,
    generate_stable_email_id,
    normalize_message_id,
    normalize_timestamp,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EmailClient:
    def __init__(self):
        self.email_address = config.EMAIL_ADDRESS
        self.email_password = config.EMAIL_PASSWORD
        self.imap_server = config.IMAP_SERVER
        self.imap_port = config.IMAP_PORT
        self.smtp_server = config.SMTP_SERVER
        self.smtp_port = config.SMTP_PORT
        self.attachment_extractor = AttachmentExtractor()
        
    @staticmethod
    def _create_ssl_context() -> ssl.SSLContext:
        """Create a TLS context with certificate verification, hostname checking, and TLS 1.2+."""
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx

    def connect_imap(self):
        """Connect to IMAP server with verified TLS."""
        try:
            mail = imaplib.IMAP4_SSL(
                self.imap_server,
                self.imap_port,
                ssl_context=self._create_ssl_context(),
            )
            mail.login(self.email_address, self.email_password)
            logger.info("Successfully connected to IMAP server")
            return mail
        except Exception as e:
            logger.error(f"Failed to connect to IMAP: {e}")
            raise
    
    @staticmethod
    def _extract_uid_validity(mail: Any) -> Optional[str]:
        """Read RFC 3501 UIDVALIDITY from an IMAP connection after SELECT INBOX."""
        try:
            typ, data = mail.response("UIDVALIDITY")
            if typ == "UIDVALIDITY" and data and data[0]:
                raw = data[0]
                return raw.decode() if isinstance(raw, bytes) else str(raw)
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_fetch_payload(msg_data: Any) -> Optional[bytes]:
        """Extract raw message or header bytes from an IMAP FETCH response list."""
        for response_part in msg_data or ():
            if isinstance(response_part, tuple) and len(response_part) > 1:
                payload = response_part[1]
                if isinstance(payload, (bytes, bytearray)):
                    return bytes(payload)
        return None

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
        """Fetch unprocessed emails from the IMAP inbox using RFC 3501 UIDs.

        Performs a lightweight header-only ``BODY.PEEK[HEADER.FIELDS ...]`` fetch
        first to skip already-processed messages before downloading full bodies
        and parsing attachments.

        Args:
            processed_ids: Set of hashed email IDs that have already been processed.
            max_emails: Maximum number of UIDs to inspect in this batch.
            offset: Batch offset (0 = newest batch).
            skip_processed: If True, exclude messages already seen by ID or Message-ID.
            exclude_ids: Additional IDs to ignore for the current check cycle.
            processed_message_ids: Normalized RFC 5322 Message-IDs already processed.

        Returns:
            Tuple of ``(emails list, has_more flag)``.
        """
        emails: List[Dict[str, Any]] = []
        has_more = False
        mail = None
        try:
            mail = self.connect_imap()
            mail.select('INBOX')
            uidvalidity = self._extract_uid_validity(mail)

            _, uid_numbers = mail.uid('search', None, 'ALL')

            if not uid_numbers or not uid_numbers[0]:
                logger.info("No emails found")
                return emails, False

            uids = [uid.decode() for uid in uid_numbers[0].split()]
            uids.reverse()  # newest first
            start = offset * max_emails
            end = start + max_emails
            batch_uids = uids[start:end]
            has_more = end < len(uids)

            for uid_str in batch_uids:
                # 1. Header-first fetch avoids downloading full MIME/attachments for seen messages
                _, header_data = mail.uid(
                    'fetch',
                    uid_str,
                    '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT FROM DATE)])',
                )
                header_bytes = self._extract_fetch_payload(header_data)
                if header_bytes:
                    hdr_msg = email.message_from_bytes(header_bytes)
                    hdr_subject = hdr_msg['subject']
                    hdr_sender = hdr_msg['from']
                    hdr_date = hdr_msg['date']
                    hdr_message_id = hdr_msg['message-id']
                    hdr_timestamp = normalize_timestamp(hdr_date)
                    norm_mid = normalize_message_id(hdr_message_id)

                    if skip_processed and processed_message_ids and norm_mid and norm_mid in processed_message_ids:
                        continue

                    if norm_mid:
                        stable_id = generate_stable_email_id(
                            message_id=hdr_message_id,
                            timestamp=hdr_timestamp or hdr_date,
                            sender=hdr_sender,
                            subject=hdr_subject,
                        )
                        if exclude_ids and stable_id in exclude_ids:
                            continue
                        if skip_processed and stable_id in processed_ids:
                            continue

                # 2. Fetch full message only for candidates not skipped by headers
                _, msg_data = mail.uid('fetch', uid_str, '(BODY.PEEK[])')
                raw_bytes = self._extract_fetch_payload(msg_data)
                if not raw_bytes:
                    continue

                msg = email.message_from_bytes(raw_bytes)
                subject = msg['subject']
                sender = msg['from']
                date = msg['date']
                message_id = msg['message-id']

                body = self._strip_reply_trail(self._get_email_body(msg), subject=subject)
                attachment_text = self.attachment_extractor.extract_from_mime_message(msg)
                if attachment_text:
                    body = f"{body}\n\n{attachment_text}" if body else attachment_text

                recipients = self._extract_recipients(msg)
                timestamp = normalize_timestamp(date)
                body_hash = fingerprint_body(body)
                legacy_id = generate_email_id(
                    message_id=message_id or uid_str,
                    timestamp=timestamp or date,
                    sender=sender,
                    subject=subject,
                    body_hash=body_hash,
                )
                resolved_id = (
                    generate_stable_email_id(
                        message_id=message_id,
                        timestamp=timestamp or date,
                        sender=sender,
                        subject=subject,
                        body_hash=body_hash,
                    )
                    if normalize_message_id(message_id)
                    else legacy_id
                )

                if exclude_ids and (resolved_id in exclude_ids or legacy_id in exclude_ids):
                    continue
                if skip_processed and (resolved_id in processed_ids or legacy_id in processed_ids):
                    continue

                emails.append({
                    'id': resolved_id,
                    'legacy_id': legacy_id,
                    'source_id': uid_str,
                    'uid': uid_str,
                    'uidvalidity': uidvalidity,
                    'message_id': message_id or uid_str,
                    'subject': subject,
                    'sender': sender,
                    'date': timestamp or date,
                    'body': body,
                    'original_msg': msg,
                    'recipients': recipients,
                    'timestamp': timestamp or date,
                })

                logger.info(f"Fetched email: {subject} from {sender}")

        except Exception as e:
            logger.error(f"Error fetching emails: {e}")
            raise
        finally:
            if mail is not None:
                try:
                    mail.close()
                except Exception:
                    pass
                try:
                    mail.logout()
                except Exception:
                    pass

        return emails, has_more
    
    def _get_email_body(self, msg) -> str:
        """Extract email body from message"""
        body = ""
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                
                if content_type == "text/plain" and "attachment" not in content_disposition:
                    try:
                        body = part.get_payload(decode=True).decode()
                        break
                    except Exception:
                        pass
                elif content_type == "text/html" and not body and "attachment" not in content_disposition:
                    try:
                        body = part.get_payload(decode=True).decode()
                    except Exception:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode()
            except Exception:
                body = str(msg.get_payload())
        
        return body

    def _strip_reply_trail(self, body: str, subject: Optional[str] = None) -> str:
        """Remove quoted previous messages and reply headers while keeping forwards intact."""
        return strip_reply_trail(body, subject=subject)

    @staticmethod
    def _extract_summary_from_ics(ics_content: str) -> Optional[str]:
        """Extract ``SUMMARY`` property from iCalendar (.ics) content if available."""
        return extract_summary_from_ics(ics_content)

    def send_reply_with_calendar(
        self,
        original_email: Dict,
        ics_content: str,
        reply_message: str = "I've created a calendar invite for this event.",
        dry_run: bool = False,
        event_data: Optional[Dict] = None,
        event_title: Optional[str] = None,
    ):
        """Send a reply to an email with calendar invite attached.

        In accordance with RFC 6047 Section 2.3, the MIME Subject header field is
        derived from the specific event title rather than the parent email subject,
        ensuring calendar views show the accurate event headline rather than the generic
        message subject.

        Args:
            original_email: Dictionary containing original email data.
            ics_content: iCalendar file content as string.
            reply_message: Message body for the reply.
            dry_run: If True, only preview without sending.
            event_data: Optional dictionary containing event details (title, etc.).
            event_title: Optional explicit title/short description of the event.

        Returns:
            Dictionary containing send result status, recipients, protocol, and subject.
        """
        try:
            subject_line = resolve_invite_subject(
                original_email,
                ics_content,
                event_data=event_data,
                event_title=event_title,
            )

            recipients = original_email.get('recipients') or []
            if not recipients:
                sender_addr = parseaddr(original_email.get('sender', ''))[1]
                if sender_addr:
                    recipients = [sender_addr]

            recipients = self._filter_allowed_recipients(recipients)

            if not recipients:
                message = "No allowable recipients found; aborting send"
                logger.warning(message)
                raise RuntimeError(message)

            msg = build_calendar_mime_message(
                sender=self.email_address,
                recipients=recipients,
                subject=subject_line,
                reply_message=reply_message,
                ics_content=ics_content,
                in_reply_to=original_email.get('message_id'),
            )

            result = {
                'recipients': recipients,
                'dry_run': bool(dry_run),
                'protocol': 'smtp',
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

            save_outbound_mime_dump(msg.as_bytes(), prefix="outbound_invite")

            # Send email (handle implicit SSL vs STARTTLS) with verified TLS
            ssl_ctx = self._create_ssl_context()
            server: Union[smtplib.SMTP, smtplib.SMTP_SSL]
            if self.smtp_port == 465:
                server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, context=ssl_ctx)
            else:
                server = smtplib.SMTP(self.smtp_server, self.smtp_port)
                server.starttls(context=ssl_ctx)
            server.login(self.email_address, self.email_password)
            server.send_message(msg)
            server.quit()

            result['status'] = 'sent'
            logger.info("Reply sent successfully to %s", ', '.join(recipients))
            return result

        except Exception as e:
            logger.error(f"Error sending reply: {e}")
            raise
    
    def _extract_recipients(self, msg) -> List[str]:
        """Collect unique participants from From/To/Cc/Reply-To headers."""
        header_values = []
        for header in ('reply-to', 'from', 'to', 'cc'):
            value = msg.get(header)
            if value:
                header_values.append(value)
        recipients: List[str] = []
        for _, addr in getaddresses(header_values):
            normalized = addr.strip()
            if not normalized:
                continue
            if normalized not in recipients:
                recipients.append(normalized)
        if not recipients:
            sender_addr = parseaddr(msg.get('from', ''))[1]
            if sender_addr:
                recipients.append(sender_addr)
        return recipients

    def _filter_allowed_recipients(self, recipients: List[str]) -> List[str]:
        """Validate recipient email addresses and optionally enforce internal domain policy."""
        return filter_allowed_recipients(recipients, self_email=getattr(self, 'email_address', ''))

    @staticmethod
    def _escape_imap_quoted(value: str) -> str:
        """Validate and escape a string for safe inclusion inside an RFC 3501 IMAP quoted-string.

        Rejects strings exceeding 512 characters or containing characters outside
        printable ASCII (0x20..0x7E) to prevent IMAP command injection.

        Args:
            value: Raw search string (e.g., Message-ID).

        Returns:
            Escaped string safe to wrap in double quotes for IMAP SEARCH.

        Raises:
            ValueError: If the input contains control/non-printable characters or exceeds max length.
        """
        if not value or len(value) > 512:
            raise ValueError("IMAP quoted string empty or exceeds 512 characters")
        if any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in value):
            raise ValueError("IMAP quoted string contains disallowed control or non-ASCII characters")
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def fetch_email_by_message_id(self, message_id: str) -> Optional[Dict]:
        """
        Fetch a single email by its Message-ID header from the IMAP inbox.

        Args:
            message_id: The RFC 2822 Message-ID string to look up.

        Returns:
            Dict containing parsed email metadata and body, or None if not found.
        """
        if not message_id:
            return None
        clean_id = message_id.strip()
        try:
            self._escape_imap_quoted(clean_id)
        except ValueError as exc:
            logger.warning("Rejected unsafe Message-ID for IMAP lookup: %s", exc)
            return None

        mail = None
        try:
            mail = self.connect_imap()
            mail.select('INBOX')
            candidates_to_try = [f'"{self._escape_imap_quoted(clean_id)}"']
            if clean_id.startswith('<') and clean_id.endswith('>'):
                candidates_to_try.append(f'"{self._escape_imap_quoted(clean_id[1:-1])}"')
            else:
                candidates_to_try.append(f'"{self._escape_imap_quoted(f"<{clean_id}>")}"')

            matching_uids = []
            for query_val in candidates_to_try:
                status, nums = mail.uid('search', None, f'HEADER Message-ID {query_val}')
                if status == 'OK' and nums and nums[0].split():
                    matching_uids = nums[0].split()
                    break

            if not matching_uids:
                logger.warning("No email found in IMAP inbox with Message-ID: %s", message_id)
                return None

            uid_str = matching_uids[-1].decode()
            _, msg_data = mail.uid('fetch', uid_str, '(BODY.PEEK[])')
            raw_bytes = self._extract_fetch_payload(msg_data)
            if raw_bytes:
                msg = email.message_from_bytes(raw_bytes)
                subject = msg['subject']
                sender = msg['from']
                date = msg['date']
                msg_id = msg['message-id']

                body = self._strip_reply_trail(self._get_email_body(msg), subject=subject)
                attachment_text = self.attachment_extractor.extract_from_mime_message(msg)
                if attachment_text:
                    body = f"{body}\n\n{attachment_text}" if body else attachment_text

                recipients = self._extract_recipients(msg)
                timestamp = normalize_timestamp(date)
                body_hash = fingerprint_body(body)
                legacy_id = generate_email_id(
                    message_id=msg_id or uid_str,
                    timestamp=timestamp or date,
                    sender=sender,
                    subject=subject,
                    body_hash=body_hash,
                )
                resolved_id = (
                    generate_stable_email_id(
                        message_id=msg_id,
                        timestamp=timestamp or date,
                        sender=sender,
                        subject=subject,
                        body_hash=body_hash,
                    )
                    if normalize_message_id(msg_id)
                    else legacy_id
                )

                return {
                    'id': resolved_id,
                    'legacy_id': legacy_id,
                    'source_id': uid_str,
                    'uid': uid_str,
                    'message_id': msg_id or message_id,
                    'subject': subject,
                    'sender': sender,
                    'date': timestamp or date,
                    'body': body,
                    'original_msg': msg,
                    'recipients': recipients,
                }
            return None
        except Exception as e:
            logger.error("Error fetching email by Message-ID %s: %s", message_id, e)
            return None
        finally:
            if mail is not None:
                try:
                    mail.close()
                except Exception:
                    pass
                try:
                    mail.logout()
                except Exception:
                    pass

    def fetch_email_by_id(
        self,
        email_id: str,
        message_id: Optional[str] = None,
        subject: Optional[str] = None,
        sender: Optional[str] = None,
        max_search: int = 100,
    ) -> Optional[Dict]:
        """
        Fetch an email from IMAP by its deterministic hash ID or message ID.

        First attempts direct lookup by Message-ID (if available). If that fails
        or produces no match, scans recent inbox messages from the IMAP server,
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

        # 2. Scan recent emails in inbox by IMAP UID to match by deterministic hash
        mail = None
        try:
            mail = self.connect_imap()
            mail.select('INBOX')
            _, uid_numbers = mail.uid('search', None, 'ALL')

            if not uid_numbers or not uid_numbers[0]:
                return None

            uids = [uid.decode() for uid in uid_numbers[0].split()]
            uids.reverse()  # newest first
            batch_uids = uids[:max_search]
            fallback_match = None

            for uid_str in batch_uids:
                _, msg_data = mail.uid('fetch', uid_str, '(BODY.PEEK[])')
                raw_bytes = self._extract_fetch_payload(msg_data)
                if not raw_bytes:
                    continue

                msg = email.message_from_bytes(raw_bytes)
                msg_subj = msg['subject']
                msg_from = msg['from']
                msg_date = msg['date']
                msg_id = msg['message-id']

                body = self._strip_reply_trail(self._get_email_body(msg), subject=msg_subj or subject)
                attachment_text = self.attachment_extractor.extract_from_mime_message(msg)
                if attachment_text:
                    body = f"{body}\n\n{attachment_text}" if body else attachment_text

                recipients = self._extract_recipients(msg)
                timestamp = normalize_timestamp(msg_date)
                body_hash = fingerprint_body(body)
                legacy_id = generate_email_id(
                    message_id=msg_id or uid_str,
                    timestamp=timestamp or msg_date,
                    sender=msg_from,
                    subject=msg_subj,
                    body_hash=body_hash,
                )
                resolved_id = (
                    generate_stable_email_id(
                        message_id=msg_id,
                        timestamp=timestamp or msg_date,
                        sender=msg_from,
                        subject=msg_subj,
                        body_hash=body_hash,
                    )
                    if normalize_message_id(msg_id)
                    else legacy_id
                )

                email_dict = {
                    'id': resolved_id,
                    'legacy_id': legacy_id,
                    'source_id': uid_str,
                    'uid': uid_str,
                    'message_id': msg_id or uid_str,
                    'subject': msg_subj,
                    'sender': msg_from,
                    'date': timestamp or msg_date,
                    'body': body,
                    'original_msg': msg,
                    'recipients': recipients,
                }

                if resolved_id == email_id or legacy_id == email_id:
                    return email_dict

                if message_id and msg_id:
                    c1 = normalize_message_id(message_id)
                    c2 = normalize_message_id(msg_id)
                    if c1 and c1 == c2 and not fallback_match:
                        fallback_match = email_dict

                if subject and sender and not fallback_match:
                    if (msg_subj or '').strip() == (subject or '').strip() and (msg_from or '').strip().lower() == (sender or '').strip().lower():
                        fallback_match = email_dict

            return fallback_match
        except Exception as exc:
            logger.error("Error scanning IMAP inbox for email %s: %s", email_id, exc)
            return None
        finally:
            if mail is not None:
                try:
                    mail.close()
                except Exception:
                    pass
                try:
                    mail.logout()
                except Exception:
                    pass

