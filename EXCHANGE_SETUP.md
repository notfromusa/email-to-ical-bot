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
```

`EXCHANGE_USERNAME` is optional. Set it only if your organization requires a login name that differs from your mailbox address.

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
