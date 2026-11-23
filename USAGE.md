# iCal Bot - Quick Reference

## Test Mode (Dry Run)

To test without sending emails, edit `.env`:
```bash
DRY_RUN=true
```

## Running the Bot

### With Web UI (Recommended — Single-Coordinator BotRuntime + Waitress WSGI)
```bash
uv run run.py
```
Then open: http://127.0.0.1:5000

First-time login:
- Visit `/setup` from localhost to set your UI username and password (**minimum 12 characters**).
- After setup, use `/login` (rate-limited to 5 failed attempts per 15 minutes per IP; password managers can store it) and use **Sign out** in the header to clear your session.

### Bot Only (No Web UI)
```bash
uv run run.py --no-web
```

### Single Check (Test Mode)
```bash
uv run run.py --once
```

### Web UI Only
```bash
uv run web_ui.py
```

## Installing Dependencies

If you haven't synced dependencies yet:
```bash
uv sync
```

## Configuration

Edit `.env` file (`chmod 600 .env`):
- `DRY_RUN=true` - Preview mode (no emails sent)
- `DRY_RUN=false` - Live mode (emails will be sent)
- `LLM_ENDPOINT=http://localhost:11434/api/chat` - Ollama endpoint (`/api/chat` with JSON Schema default; legacy `/api/generate` also supported)
- `LLM_DEFAULT_DURATION_MINUTES=60` - Default duration when an invite omits an end time
- `LLM_DURATION_RULES_JSON` - Optional JSON array of custom keyword duration rules
- `WEB_UI_PORT=5000` - Change web UI port
- `CHECK_INTERVAL_SECONDS=300` - How often to check emails
- `REQUIRE_HTTPS_FOR_REMOTE=true` - Require HTTPS for non-local access
- `REQUIRE_HTTPS_FOR_LLM=true` - Require HTTPS for non-local LLM endpoints
- `TRUSTED_PROXY_COUNT=0` - Number of trusted reverse proxies (`0` disables trusting `X-Forwarded-Proto`)
- `SESSION_COOKIE_SECURE=false` - Force `Secure` session cookie (auto-enabled on HTTPS)
- `SESSION_LIFETIME_HOURS=12` - Session lifetime in hours
- `ENFORCE_INTERNAL_RECIPIENT_DOMAIN=false` - Enforce `INTERNAL_EMAIL_DOMAIN` on outbound recipients
- `AUTO_APPROVE_ALLOW_EXTERNAL_RECIPIENTS=false` - Keep manual approval required for external recipients
- `EXCHANGE_AUTODISCOVER=false` - Prefer explicit `EXCHANGE_SERVER` over EWS Autodiscover

## State & Verification

- Processed email state, IMAP UID sync watermarks, results, and approval logs are unified in SQLite (`data/processed_results.db` in WAL mode). Any legacy `processed_emails.json` file is imported automatically once on startup and left untouched.
- Run the test and static verification suite with:
  ```bash
  uv run pytest -q
  node --test tests/js/test_frontend_modules.mjs
  ```

## Web Dashboard Features

- 📊 **Statistics** - Total events, sent vs dry-run
- 📅 **Event History** - View all detected events
- 🔄 **Auto-refresh** - Updates every 10 seconds
- ⚠️ **Status Indicators** - Shows if in dry-run or live mode
