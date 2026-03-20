#!/usr/bin/env python3
"""
Run iCal Bot with Web UI
Starts both the email processing bot and the monitoring web interface
"""

import sys
import threading
import time
import logging

import config
from main import iCalBot
from web_ui import run_web_ui

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_bot(once=False):
    """Run the bot in a separate thread"""
    bot = iCalBot()
    if once:
        bot.run_once()
    else:
        bot.run_continuous()


def wait_for_credentials_and_start():
    """Wait for mailbox credentials to be set via web UI, then start bot."""
    logger.info("Waiting for mailbox credentials via web UI...")
    while not getattr(config, 'EMAIL_PASSWORD', ''):
        time.sleep(1)
    logger.info("Mailbox credentials received. Starting bot thread...")
    run_bot(once=False)


def main():
    """Main entry point"""
    once = '--once' in sys.argv
    no_web = '--no-web' in sys.argv
    
    if config.DRY_RUN:
        logger.warning("=" * 60)
        logger.warning("DRY RUN MODE ENABLED")
        logger.warning("Emails will NOT be sent - only previewed")
        logger.warning("To disable: Set DRY_RUN=false in .env")
        logger.warning("=" * 60)
    
    if no_web or once:
        # Run bot only
        logger.info("Starting bot without web UI")
        run_bot(once=once)
    else:
        # Run both bot and web UI
        logger.info("Starting iCal Bot with Web UI...")
        logger.info(f"Web UI will be available at: http://{config.WEB_UI_HOST}:{config.WEB_UI_PORT}")
        
        if getattr(config, 'EMAIL_PASSWORD', ''):
            # Start bot in background thread
            bot_thread = threading.Thread(target=run_bot, daemon=True)
            bot_thread.start()
        else:
            # Wait for credentials before starting
            waiter_thread = threading.Thread(target=wait_for_credentials_and_start, daemon=True)
            waiter_thread.start()
        
        # Give bot a moment to start
        time.sleep(2)
        
        # Start web UI in main thread
        try:
            run_web_ui()
        except KeyboardInterrupt:
            logger.info("\nShutting down...")


if __name__ == "__main__":
    main()
