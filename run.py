#!/usr/bin/env python3
"""Run iCal Bot alongside the Waitress-hosted monitoring Web UI."""

from __future__ import annotations

import logging
import signal
import sys

import config
from ical_bot.runtime import BotRuntime, serve_wsgi
from web_ui import create_wsgi_app, runtime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress verbose HTTP request access logs from Werkzeug/Waitress
logging.getLogger("werkzeug").setLevel(logging.WARNING)

active_runtime: BotRuntime = runtime


def run_bot(once: bool = False) -> None:
    """Run the bot directly without starting the HTTP UI server.

    Args:
        once: When True, execute a single inbox check and exit.
    """
    if once:
        active_runtime.run_check_once(blocking=True)
    else:
        active_runtime.start_background_polling()
        try:
            while True:
                signal.pause()
        except (AttributeError, KeyboardInterrupt, SystemExit):
            active_runtime.stop()


def main() -> None:
    """Entry point coordinating single-run, headless worker, or combined UI mode."""
    once = "--once" in sys.argv
    no_web = "--no-web" in sys.argv

    if config.DRY_RUN:
        logger.warning("=" * 60)
        logger.warning("DRY RUN MODE ENABLED")
        logger.warning("Emails will NOT be sent - only previewed")
        logger.warning("To disable: Set DRY_RUN=false in .env")
        logger.warning("=" * 60)

    def handle_shutdown(signum, frame) -> None:
        del frame
        logger.info("Signal %s received. Shutting down gracefully...", signum)
        active_runtime.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    if no_web or once:
        logger.info("Starting bot without web UI")
        run_bot(once=once)
        return

    logger.info("Starting iCal Bot with Web UI...")
    logger.info("Web UI will be available at: http://%s:%s", config.WEB_UI_HOST, config.WEB_UI_PORT)

    wsgi_app = create_wsgi_app(start_bot=True)
    try:
        serve_wsgi(wsgi_app, config.WEB_UI_HOST, config.WEB_UI_PORT)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
        active_runtime.stop()


if __name__ == "__main__":
    main()
