"""
Calendar invite generator for creating iCalendar (.ics) files
"""

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional
import uuid
import logging

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CalendarGenerator:
    def __init__(self):
        configured_tz = (getattr(config, 'DEFAULT_TIMEZONE', '') or '').strip()
        if not configured_tz or configured_tz.upper() == 'UTC':
            configured_tz = 'Europe/Berlin'
        self.timezone = configured_tz
        self.timezone_info = self._resolve_timezone(self.timezone)
        self.organizer_name = config.ORGANIZER_NAME
        # Allow organizer identity to differ from the sending mailbox so self-attendees can accept
        self.organizer_email = getattr(config, 'ORGANIZER_EMAIL', config.EMAIL_ADDRESS)

    @staticmethod
    def _resolve_timezone(timezone_name: str):
        if ZoneInfo is None:
            return timezone.utc
        try:
            return ZoneInfo(timezone_name)
        except Exception:
            logger.warning("Unknown timezone '%s', falling back to UTC", timezone_name)
            return timezone.utc
        
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
            # Generate unique ID for the event
            event_uid = str(uuid.uuid4())
            
            # Parse datetime strings
            start_dt = self._parse_datetime(event_data['start_datetime'])
            end_dt = self._parse_datetime(event_data['end_datetime'])
            
            # Get current timestamp for DTSTAMP
            now = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
            
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
                f"DESCRIPTION:{self._escape_text(event_data.get('description', ''))}",
            ]
            
            # Add location if provided
            if event_data.get('location'):
                ics_content.append(f"LOCATION:{self._escape_text(event_data['location'])}")

            if attendee_lines:
                ics_content.extend(attendee_lines)
            
            # Add organizer
            ics_content.extend([
                f"ORGANIZER;CN={self.organizer_name}:mailto:{organizer_email}",
                "STATUS:CONFIRMED",
                "SEQUENCE:0",
                "END:VEVENT",
                "END:VCALENDAR"
            ])
            
            ics_string = "\r\n".join(ics_content)
            
            logger.info(f"Created iCalendar invite for: {event_data['title']}")
            
            return ics_string
            
        except Exception as e:
            logger.error(f"Error creating iCalendar invite: {e}")
            raise
    
    def _parse_datetime(self, datetime_str: str) -> datetime:
        """Parse ISO format datetime string"""
        try:
            # Remove timezone suffix if present and parse
            normalized = (datetime_str or '').strip().replace('Z', '+00:00')
            if 'T' not in normalized and ' ' in normalized:
                normalized = normalized.replace(' ', 'T', 1)
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self.timezone_info)
            return dt
        except Exception as e:
            logger.error(f"Error parsing datetime '{datetime_str}': {e}")
            raise
    
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
        Escape special characters in iCalendar text fields
        """
        if not text:
            return ""
        
        # Replace special characters according to iCalendar spec
        text = text.replace('\\', '\\\\')
        text = text.replace(';', '\\;')
        text = text.replace(',', '\\,')
        text = text.replace('\n', '\\n')
        
        return text
    
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
        lines: List[str] = []
        for email in self._normalize_recipient_list(recipients):
            cn = self._escape_text(self._guess_display_name(email))
            email_value = self._escape_text(email)
            lines.append(
                f"ATTENDEE;CN={cn};ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{email_value}"
            )
        return lines

    def _normalize_recipient_list(self, recipients: Optional[Iterable[str]]) -> List[str]:
        unique: List[str] = []
        if not recipients:
            return unique
        for raw in recipients:
            addr = (raw or '').strip()
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
