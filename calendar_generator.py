"""
Calendar invite generator for creating iCalendar (.ics) files
"""

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional
import re
import uuid
import logging

import config
from ical_bot.time_utils import (
    normalize_timezone_name,
    parse_iso_datetime,
    resolve_timezone,
    utc_stamp,
)
from messaging import build_calendar_description

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CalendarGenerator:
    """Create RFC 5545 compliant iCalendar (.ics) event invitations."""

    def __init__(self):
        """Initialize calendar generator with configured timezone and organizer identity."""
        self.timezone = normalize_timezone_name()
        self.timezone_info = self._resolve_timezone(self.timezone)
        self.organizer_name = config.ORGANIZER_NAME
        # Allow organizer identity to differ from the sending mailbox so self-attendees can accept
        self.organizer_email = getattr(config, 'ORGANIZER_EMAIL', config.EMAIL_ADDRESS)

    @staticmethod
    def _resolve_timezone(timezone_name: str):
        """Resolve an IANA timezone string via shared time utilities."""
        return resolve_timezone(timezone_name)
        
    @staticmethod
    def _sanitize_header_value(text: Optional[str]) -> str:
        """Strip CR, LF, and NUL control characters from iCalendar property values."""
        if not text:
            return ""
        return re.sub(r"[\r\n\x00]+", "", str(text)).strip()

    @staticmethod
    def _sanitize_param_value(text: Optional[str]) -> str:
        """Sanitize an iCalendar parameter value (RFC 5545 Section 3.2) to prevent injection."""
        if not text:
            return ""
        return re.sub(r'[\r\n\x00";:]+', " ", str(text)).strip()

    def create_ics_invite(self, event_data: Dict, recipients: Optional[Iterable[str]] = None) -> str:
        """
        Create an iCalendar (.ics) file content from event data
        
        Args:
            event_data: Dictionary containing event details
                - title: str
                - description: str
                - start_datetime: str (ISO format)
                - end_datetime: str (ISO format)
                - location: str (optional)
                
        Returns:
            String containing the .ics file content
        """
        try:
            # Use stable event UID if provided (e.g. for updates/resends), otherwise generate fresh UUID (RFC 5545 Section 3.8.4.7)
            event_uid = self._sanitize_header_value(event_data.get('uid')) or str(uuid.uuid4())
            sequence = int(event_data.get('sequence', 0))
            
            # Parse datetime strings
            start_dt = self._parse_datetime(event_data['start_datetime'])
            end_dt = self._parse_datetime(event_data['end_datetime'])
            
            # Get current timestamp for DTSTAMP
            now = utc_stamp('%Y%m%dT%H%M%SZ')
            
            # Format datetimes for iCalendar
            start_str = self._format_datetime(start_dt)
            end_str = self._format_datetime(end_dt)
            
            # Select organizer address; if it matches an attendee, pick a synthetic organizer to keep Outlook accept/decline enabled
            organizer_email = self.organizer_email
            normalized_recipients = self._normalize_recipient_list(recipients)
            if organizer_email and normalized_recipients:
                if organizer_email.lower() in [r.lower() for r in normalized_recipients]:
                    fallback_domain = getattr(config, 'INTERNAL_EMAIL_DOMAIN', None)
                    if fallback_domain:
                        organizer_email = f"organizer-bot@{fallback_domain}"
                    else:
                        organizer_email = "organizer-bot@example.com"

            safe_organizer_name = self._sanitize_param_value(self.organizer_name) or "iCal Bot"
            safe_organizer_email = self._sanitize_header_value(organizer_email)
            attendee_lines = self._build_attendee_lines(normalized_recipients)

            ics_content = [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//iCal Bot//Event Creator//EN",
                "CALSCALE:GREGORIAN",
                "METHOD:REQUEST",
                "BEGIN:VEVENT",
                f"UID:{event_uid}",
                f"DTSTAMP:{now}",
                f"DTSTART:{start_str}",
                f"DTEND:{end_str}",
                f"SUMMARY:{self._escape_text(event_data['title'])}",
                f"DESCRIPTION:{self._escape_text(build_calendar_description(event_data))}",
            ]
            
            # Add location if provided
            if event_data.get('location'):
                ics_content.append(f"LOCATION:{self._escape_text(event_data['location'])}")

            if attendee_lines:
                ics_content.extend(attendee_lines)
            
            # Add organizer and sequence
            ics_content.extend([
                f"ORGANIZER;CN={safe_organizer_name}:mailto:{safe_organizer_email}",
                "STATUS:CONFIRMED",
                f"SEQUENCE:{sequence}",
                "END:VEVENT",
                "END:VCALENDAR"
            ])
            
            ics_string = "\r\n".join(self._fold_line(line) for line in ics_content)
            
            logger.info(f"Created iCalendar invite for: {event_data['title']}")
            
            return ics_string
            
        except Exception as e:
            logger.error(f"Error creating iCalendar invite: {e}")
            raise
    
    def _parse_datetime(self, datetime_str: str) -> datetime:
        """Parse an ISO format datetime string using the configured default timezone."""
        dt = parse_iso_datetime(datetime_str, default_tz=self.timezone_info)
        if dt is None:
            logger.error(f"Error parsing datetime '{datetime_str}'")
            raise ValueError(f"Invalid datetime string: {datetime_str!r}")
        return dt
    
    def _format_datetime(self, dt: datetime) -> str:
        """
        Format datetime for iCalendar
        Returns format: YYYYMMDDTHHmmSSZ for UTC or YYYYMMDDTHHmmSS for local
        """
        # Convert to UTC for simplicity
        dt_utc = dt if dt.tzinfo else dt.replace(tzinfo=self.timezone_info)
        dt_utc = dt_utc.astimezone(timezone.utc)
        return dt_utc.strftime('%Y%m%dT%H%M%SZ')
    
    def _escape_text(self, text: str) -> str:
        """
        Escape special characters in iCalendar text fields according to RFC 5545 Section 3.3.11.
        """
        if not text:
            return ""
        
        # Replace special characters according to iCalendar spec
        text = text.replace('\\', '\\\\')
        text = text.replace(';', '\\;')
        text = text.replace(',', '\\,')
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        text = text.replace('\n', '\\n')
        
        return text

    @staticmethod
    def _fold_line(line: str, limit: int = 75) -> str:
        """
        Fold a content line according to RFC 5545 Section 3.1.

        Lines of text SHOULD NOT be longer than 75 octets, excluding the line break.
        Long content lines are split into lines where continuation lines begin with
        a single space character. Preserves multi-byte UTF-8 character boundaries.

        Args:
            line: The content line string to fold.
            limit: Maximum octet length per line (default: 75).

        Returns:
            Folded content line string.
        """
        encoded = line.encode('utf-8')
        if len(encoded) <= limit:
            return line

        parts = []
        remaining = encoded
        first_line = True

        while remaining:
            max_bytes = limit if first_line else limit - 1
            if len(remaining) <= max_bytes:
                parts.append(remaining.decode('utf-8'))
                break

            # Find valid UTF-8 boundary within max_bytes
            cut = max_bytes
            while cut > 0:
                try:
                    chunk = remaining[:cut].decode('utf-8')
                    break
                except UnicodeDecodeError:
                    cut -= 1

            if cut == 0:
                chunk = remaining[:1].decode('utf-8', errors='ignore')
                cut = 1

            parts.append(chunk)
            remaining = remaining[cut:]
            first_line = False

        return "\r\n ".join(parts)
    
    def validate_event_data(self, event_data: Dict) -> bool:
        """
        Validate that event data contains all required fields
        
        Args:
            event_data: Dictionary to validate
            
        Returns:
            True if valid, False otherwise
        """
        required_fields = ['title', 'start_datetime', 'end_datetime']
        
        for field in required_fields:
            if field not in event_data or not event_data[field]:
                logger.error(f"Missing required field: {field}")
                return False
        
        # Validate datetime formats
        try:
            start_dt = self._parse_datetime(event_data['start_datetime'])
            end_dt = self._parse_datetime(event_data['end_datetime'])
        except Exception as e:
            logger.error(f"Invalid datetime format: {e}")
            return False

        if not self._is_future_event(start_dt):
            logger.warning("Event start is in the past; skipping invite generation")
            return False

        if end_dt <= start_dt:
            logger.error("Event end must be after start time")
            return False
        
        return True

    def _is_future_event(self, start_dt: datetime) -> bool:
        """Ensure the proposed event start is not in the past."""
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=self.timezone_info)
        now_utc = datetime.now(timezone.utc)
        return start_dt.astimezone(timezone.utc) >= now_utc

    def _build_attendee_lines(self, recipients: Optional[Iterable[str]]) -> List[str]:
        """Format RFC 5545 ATTENDEE lines with sanitized CN parameters and mailto values."""
        lines: List[str] = []
        for email in self._normalize_recipient_list(recipients):
            cn = self._sanitize_param_value(self._escape_text(self._guess_display_name(email)))
            email_value = self._sanitize_header_value(self._escape_text(email))
            if not email_value:
                continue
            lines.append(
                f"ATTENDEE;CN={cn};ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{email_value}"
            )
        return lines

    def _normalize_recipient_list(self, recipients: Optional[Iterable[str]]) -> List[str]:
        """Deduplicate and strip CR/LF/NUL control characters from recipient addresses."""
        unique: List[str] = []
        if not recipients:
            return unique
        for raw in recipients:
            addr = self._sanitize_header_value(raw)
            if not addr:
                continue
            if addr not in unique:
                unique.append(addr)
        return unique

    @staticmethod
    def _guess_display_name(email: str) -> str:
        local_part = (email or '').split('@')[0]
        if not local_part:
            return email
        tokens = [token for token in local_part.replace('.', ' ').replace('_', ' ').split(' ') if token]
        if not tokens:
            return email
        return ' '.join(token.capitalize() for token in tokens)
