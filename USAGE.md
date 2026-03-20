# iCal Bot - Quick Reference

## Test Mode (Dry Run)

To test without sending emails, edit `.env`:
```bash
DRY_RUN=true
```

## Running the Bot

### With Web UI (Recommended for monitoring)
```bash
source .venv/bin/activate
python run.py
```
Then open: http://127.0.0.1:5000

First-time login:
- Visit /setup from localhost to set your UI username and password.
- After setup, use /login (password managers can store it).

### Bot Only (No Web UI)
```bash
source .venv/bin/activate
python run.py --no-web
```

### Single Check (Test Mode)
```bash
source .venv/bin/activate
python run.py --once
```

### Web UI Only
```bash
source .venv/bin/activate
python web_ui.py
```

## Installing Flask

If you haven't already:
```bash
source .venv/bin/activate
uv pip install -e .
```

## Configuration

Edit `.env` file:
- `DRY_RUN=true` - Preview mode (no emails sent)
- `DRY_RUN=false` - Live mode (emails will be sent)
- `WEB_UI_PORT=5000` - Change web UI port
- `CHECK_INTERVAL_SECONDS=300` - How often to check emails
- `REQUIRE_HTTPS_FOR_REMOTE=true` - Require HTTPS for non-local access
- `REQUIRE_HTTPS_FOR_LLM=true` - Require HTTPS for non-local LLM endpoints

## Web Dashboard Features

- 📊 **Statistics** - Total events, sent vs dry-run
- 📅 **Event History** - View all detected events
- 🔄 **Auto-refresh** - Updates every 10 seconds
- ⚠️ **Status Indicators** - Shows if in dry-run or live mode
