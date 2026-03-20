"""
Email client for reading and sending emails
"""

import imaplib
import smtplib
import email
import re
from pathlib import Path
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.utils import parseaddr, getaddresses
from typing import List, Dict, Optional, Tuple
import logging

import config
from attachment_parser import AttachmentExtractor
from identifiers import generate_email_id, fingerprint_body, normalize_timestamp
from messaging import build_reply_message_html_from_text

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
        
    def connect_imap(self):
        """Connect to IMAP server"""
        try:
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            mail.login(self.email_address, self.email_password)
            logger.info("Successfully connected to IMAP server")
            return mail
        except Exception as e:
            logger.error(f"Failed to connect to IMAP: {e}")
            raise
    
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
            skip_processed: If True, exclude IDs found in processed_ids
            exclude_ids: Additional IDs to ignore (per run)
            
        Returns:
            Tuple of (emails list, has_more flag)
        """
        emails = []
        has_more = False
        try:
            mail = self.connect_imap()
            mail.select('INBOX')
            
            # Search for all unseen emails or recent emails
            _, message_numbers = mail.search(None, 'ALL')
            
            if not message_numbers[0]:
                logger.info("No emails found")
                return emails, False
            
            email_ids = [msg_id.decode() for msg_id in message_numbers[0].split()]
            email_ids.reverse()  # newest first
            start = offset * max_emails
            end = start + max_emails
            batch_ids = email_ids[start:end]
            has_more = end < len(email_ids)
            
            for email_id_str in batch_ids:
                _, msg_data = mail.fetch(email_id_str, '(BODY.PEEK[])')
                
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        
                        subject = msg['subject']
                        sender = msg['from']
                        date = msg['date']
                        message_id = msg['message-id']
                        
                        # Extract email body and trim quoted history
                        body = self._strip_reply_trail(self._get_email_body(msg))
                        attachment_text = self.attachment_extractor.extract_from_mime_message(msg)
                        if attachment_text:
                            body = f"{body}\n\n{attachment_text}" if body else attachment_text
                        
                        recipients = self._extract_recipients(msg)

                        timestamp = normalize_timestamp(date)
                        body_hash = fingerprint_body(body)
                        hashed_id = generate_email_id(
                            message_id=message_id or email_id_str,
                            timestamp=timestamp or date,
                            sender=sender,
                            subject=subject,
                            body_hash=body_hash,
                        )

                        if exclude_ids and hashed_id in exclude_ids:
                            continue
                        if skip_processed and hashed_id in processed_ids:
                            continue

                        emails.append({
                            'id': hashed_id,
                            'source_id': email_id_str,
                            'message_id': message_id or email_id_str,
                            'subject': subject,
                            'sender': sender,
                            'date': timestamp or date,
                            'body': body,
                            'original_msg': msg,
                            'recipients': recipients,
                            'timestamp': timestamp or date,
                        })
                        
                        logger.info(f"Fetched email: {subject} from {sender}")
            
            mail.close()
            mail.logout()
            
        except Exception as e:
            logger.error(f"Error fetching emails: {e}")
            raise
        
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
            # Build a mixed container so we can include both inline calendar part and a file attachment
            msg = MIMEMultipart('mixed')
            msg['From'] = self.email_address
            msg['Subject'] = f"Re: {original_email['subject']}"

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

            msg['To'] = ', '.join(recipients)
            
            # Add threading headers to keep it in the same conversation
            if original_email.get('message_id'):
                msg['In-Reply-To'] = original_email['message_id']
                msg['References'] = original_email['message_id']
            
            # Multipart/alternative keeps text, HTML, and calendar inline so Outlook can surface buttons
            alt = MIMEMultipart('alternative')
            msg.attach(alt)

            # Plain/text version
            alt.attach(MIMEText(reply_message, 'plain', 'utf-8'))

            # HTML version with preserved line breaks
            alt.attach(MIMEText(build_reply_message_html_from_text(reply_message), 'html', 'utf-8'))

            # Inline calendar part (critical for Outlook meeting actions)
            calendar_part = MIMEText(ics_content, 'calendar', 'utf-8')
            calendar_part.replace_header('Content-Type', 'text/calendar; method=REQUEST; charset="UTF-8"')
            calendar_part.add_header('Content-Disposition', 'inline')
            calendar_part.add_header('Content-Class', 'urn:content-classes:calendarmessage')
            alt.attach(calendar_part)

            # Add a separate attachment so non-Outlook clients can save the .ics
            ics_attachment = MIMEBase('text', 'calendar', method='REQUEST', name='invite.ics')
            ics_attachment.set_payload(ics_content)
            encoders.encode_base64(ics_attachment)
            ics_attachment.add_header('Content-Disposition', 'attachment', filename='invite.ics')
            msg.attach(ics_attachment)
            
            result = {
                'recipients': recipients,
                'dry_run': bool(dry_run),
                'protocol': 'smtp',
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
            
            # Persist MIME for debugging
            if getattr(config, 'ENABLE_DEBUG_DUMPS', False):
                try:
                    dump_dir = Path(config.RESULTS_DB_PATH).parent if hasattr(config, 'RESULTS_DB_PATH') else Path('.')
                    dump_path = dump_dir / f"outbound_invite_{datetime.utcnow().strftime('%Y%m%d_%H%M%S%f')}.eml"
                    dump_path.write_bytes(msg.as_bytes())
                    logger.info("Saved outbound MIME to %s", dump_path)
                except Exception as exc:
                    logger.warning("Failed to save outbound MIME: %s", exc)

            # Send email (handle implicit SSL vs STARTTLS)
            if self.smtp_port == 465:
                server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port)
            else:
                server = smtplib.SMTP(self.smtp_server, self.smtp_port)
                server.starttls()
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
