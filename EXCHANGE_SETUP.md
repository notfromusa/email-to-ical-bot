# Exchange Setup Guide

## Quick Start

Edit your `.env` file with Exchange settings:

```bash
# Email Configuration
EMAIL_ADDRESS=your-email@example.com
EMAIL_PASSWORD=your-password-or-app-password
EMAIL_PROTOCOL=EXCHANGE

# Exchange Configuration
EXCHANGE_SERVER=outlook.office365.com
EXCHANGE_VERSION=Office365
EXCHANGE_EMAIL=your-email@example.com
EXCHANGE_USERNAME=
EXCHANGE_AUTODISCOVER=true

# Exchange Direct Calendar Configuration (Option A: native Outlook editability)
EXCHANGE_DIRECT_CALENDAR_CREATION=true
EXCHANGE_CALENDAR_INVITE_ATTENDEES=true
EXCHANGE_SEND_CONFIRMATION_EMAIL=false
CALENDAR_ITEM_CATEGORY="iCal-Bot"
```

`EXCHANGE_USERNAME` is optional. Set it only if your organization requires a login name that differs from your mailbox address.

## Calendar Integration & Outlook Editability

### The Editability Problem (RFC 5546)
Under standard iCalendar interoperability protocols ([RFC 5546 Section 1.4](https://www.rfc-editor.org/rfc/rfc5546)), only the meeting `ORGANIZER` can modify event times, locations, and descriptions. Attendees only have permission to reply (`ACCEPT`/`DECLINE`) or counter-propose times.
- If the bot emails an `.ics` with a bot identity as the organizer, Outlook marks the event **read-only** for you.
- If the bot emails an `.ics` listing *you* as the organizer, Exchange silently drops the invite because incoming meeting requests sent to oneself are not added to the organizer's calendar.

### Direct Calendar Creation (Option A)
When `EXCHANGE_DIRECT_CALENDAR_CREATION=true` (enabled by default):
- The bot creates the event directly in your Exchange primary calendar (`account.calendar`) using EWS `CalendarItem.save()`.
- **You are the meeting organizer**: You can edit the title, drag-and-drop reschedule the time, modify notes, or delete the event natively in Outlook (desktop, web, or mobile).
- **In-place updates**: When you edit details or re-send an invite from the Web UI, the bot updates the existing Exchange calendar item in-place using its item ID, preventing duplicate calendar entries.
- **Visual tagging**: Events receive the ` (iCal-Bot)` title suffix and the `iCal-Bot` Outlook category badge.

### Inviting External Attendees
Controlled via `EXCHANGE_CALENDAR_INVITE_ATTENDEES`:
- `EXCHANGE_CALENDAR_INVITE_ATTENDEES=true`: Any external recipients checked in the Web UI recipient picker receive meeting invitations directly from your Exchange mailbox (`SEND_TO_ALL_AND_SAVE_COPY`).
- If you deselect all external recipients in the UI (or set `EXCHANGE_CALENDAR_INVITE_ATTENDEES=false`), the event is saved as an appointment only for yourself (`SEND_TO_NONE`), sending zero outgoing emails.

## Test the Connection

```bash
source .venv/bin/activate
python troubleshoot.py
```

## Common Exchange Servers

### Microsoft 365 / Exchange Online
```
EXCHANGE_SERVER=outlook.office365.com
EXCHANGE_VERSION=Office365
EXCHANGE_AUTODISCOVER=true
```

### On-Premise Exchange 2016
```
EXCHANGE_SERVER=mail.example.com
EXCHANGE_VERSION=Exchange2016
EXCHANGE_AUTODISCOVER=false
```

### On-Premise Exchange 2019
```
EXCHANGE_SERVER=mail.example.com
EXCHANGE_VERSION=Exchange2019
EXCHANGE_AUTODISCOVER=false
```

## Autodiscover

**Recommended**: set `EXCHANGE_AUTODISCOVER=true`.

Only disable it if:
- You have a fixed Exchange server hostname
- Autodiscover is blocked by network policy
- You already confirmed manual server settings with your admin

## Troubleshooting

### "Autodiscover failed"
```bash
EXCHANGE_AUTODISCOVER=false
EXCHANGE_SERVER=outlook.office365.com
EXCHANGE_VERSION=Office365
```

### "Authentication failed"
- Confirm the mailbox address in `EXCHANGE_EMAIL`
- Confirm password/app-password requirements
- If needed, set `EXCHANGE_USERNAME` explicitly

### "Connection timeout"
- Verify Exchange server DNS/network reachability
- Check firewall/proxy restrictions

### "Module not found: exchangelib"
```bash
source .venv/bin/activate
pip install -e .
```

## Run the Bot

Once the connection test passes:

```bash
# Test mode (dry run - no emails sent)
source .venv/bin/activate
python run.py --once

# Full mode with web UI
python run.py
```

## Switching Between IMAP and Exchange

Set `EMAIL_PROTOCOL` in `.env`:
- `EMAIL_PROTOCOL=IMAP` for standard IMAP/SMTP providers
- `EMAIL_PROTOCOL=EXCHANGE` for Microsoft Exchange
