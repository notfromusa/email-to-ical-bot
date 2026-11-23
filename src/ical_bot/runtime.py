"""Single-coordinator runtime lifecycle for polling, manual checks, and WSGI serving."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

import waitress

import config
from ical_bot.time_utils import utc_now_iso
from workload_monitor import get_workload_monitor

if TYPE_CHECKING:
    from main import iCalBot
    from storage import StorageManager

logger = logging.getLogger(__name__)


class BotRuntime:
    """Coordinate background polling and manual inbox checks behind one lock and bot instance."""

    def __init__(
        self,
        *,
        storage: Optional["StorageManager"] = None,
        bot_factory: Optional[Callable[[], "iCalBot"]] = None,
    ) -> None:
        """Initialize the runtime coordinator.

        Args:
            storage: Optional shared :class:`~storage.StorageManager` passed to ``iCalBot``.
            bot_factory: Optional callable constructing an ``iCalBot`` instance.
        """
        self._storage = storage
        self._bot_factory = bot_factory
        self._check_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._bot: Optional["iCalBot"] = None
        self._credentials_dirty = False
        self._poll_thread: Optional[threading.Thread] = None
        self._is_checking = False
        self._last_check_at: Optional[str] = None
        self._last_error: Optional[str] = None

    @property
    def is_checking(self) -> bool:
        """Return True while either a scheduled or manual email check holds the check lock."""
        with self._state_lock:
            return self._is_checking or self._check_lock.locked()

    @property
    def last_check_at(self) -> Optional[str]:
        """Return the ISO-8601 UTC timestamp of the last completed inbox check."""
        with self._state_lock:
            return self._last_check_at

    @property
    def last_error(self) -> Optional[str]:
        """Return the sanitized error message from the most recent failed check, if any."""
        with self._state_lock:
            return self._last_error

    def invalidate_bot(self) -> None:
        """Mark cached bot adapters stale so updated credentials take effect on the next check."""
        with self._state_lock:
            self._credentials_dirty = True
            self._bot = None

    def _get_or_create_bot(self) -> "iCalBot":
        """Return the active ``iCalBot`` instance, instantiating it lazily when needed."""
        with self._state_lock:
            if self._bot is None or self._credentials_dirty:
                if self._bot_factory is not None:
                    self._bot = self._bot_factory()
                else:
                    from main import iCalBot

                    self._bot = iCalBot(storage=self._storage)
                self._credentials_dirty = False
            return self._bot

    def _execute_locked_check(self) -> None:
        """Run ``bot.run_once()`` assuming ``_check_lock`` is already held by the caller."""
        with self._state_lock:
            self._is_checking = True
        try:
            bot = self._get_or_create_bot()
            bot.run_once()
            with self._state_lock:
                self._last_check_at = utc_now_iso()
                self._last_error = None
        except Exception as exc:
            logger.error("Email check failed: %s", exc)
            with self._state_lock:
                self._last_error = "Manual email check failed. Check server logs."
        finally:
            with self._state_lock:
                self._is_checking = False

    def run_check_once(self, *, blocking: bool = True) -> bool:
        """Execute a single inbox check under the exclusive coordinator lock.

        Args:
            blocking: Wait for an in-flight check when True; return False immediately if False.

        Returns:
            True if this invocation ran the check, False if skipped because the lock was busy.
        """
        acquired = self._check_lock.acquire(blocking=blocking)
        if not acquired:
            return False
        try:
            self._execute_locked_check()
            return True
        finally:
            self._check_lock.release()

    def trigger_manual_check(self) -> Tuple[bool, Optional[str]]:
        """Trigger an asynchronous manual email check if credentials are ready and lock is free.

        Returns:
            Tuple of ``(started, error_message)``.
        """
        if not config.has_email_password():
            return False, "Mailbox credentials are not configured"

        acquired = self._check_lock.acquire(blocking=False)
        if not acquired:
            return False, "Email check already in progress"

        with self._state_lock:
            self._is_checking = True

        def _worker() -> None:
            try:
                self._execute_locked_check()
            finally:
                self._check_lock.release()

        threading.Thread(target=_worker, daemon=True, name="ical-manual-check").start()
        return True, None

    def start_background_polling(self) -> None:
        """Start the daemon background polling loop once per runtime instance."""
        with self._state_lock:
            if self._poll_thread is not None and self._poll_thread.is_alive():
                return
            self._stop_event.clear()
            thread = threading.Thread(target=self._poll_loop, daemon=True, name="ical-bot-poller")
            self._poll_thread = thread
            thread.start()

    def _poll_loop(self) -> None:
        """Wait for credentials and run periodic inbox checks until stopped."""
        monitor = get_workload_monitor()
        if not config.has_email_password():
            logger.info("Waiting for mailbox credentials via web UI...")
            while not self._stop_event.is_set() and not config.has_email_password():
                self._stop_event.wait(1.0)
            if not self._stop_event.is_set():
                logger.info("Mailbox credentials received. Starting background polling...")

        logger.info("iCal Bot background polling started (interval: %ss)", config.CHECK_INTERVAL_SECONDS)
        try:
            while not self._stop_event.is_set():
                self.run_check_once(blocking=True)
                if self._stop_event.is_set():
                    break

                with self._state_lock:
                    bot = self._bot

                has_pending = bool(bot and getattr(bot, "has_pending_retries", False))
                if has_pending or monitor.is_in_cooldown():
                    logger.info("Pending retries or active cooldown detected. Waiting for LLM availability...")
                    while not self._stop_event.is_set() and monitor.is_in_cooldown():
                        rem = monitor.get_cooldown_remaining()
                        logger.info("Cooldown active (%ds remaining). Waiting...", rem)
                        self._stop_event.wait(min(15, max(1, rem)))

                    while not self._stop_event.is_set() and not monitor.is_system_idle():
                        logger.info("Workload still active on workstation. Yielding...")
                        self._stop_event.wait(15.0)

                    if not self._stop_event.is_set() and has_pending:
                        logger.info("LLM is available again. Triggering immediate retry of postponed emails...")
                        continue

                self._stop_event.wait(max(1, int(getattr(config, "CHECK_INTERVAL_SECONDS", 300))))
        finally:
            try:
                monitor.unload_model()
            except Exception:
                pass
            logger.info("Bot polling loop stopped cleanly.")

    def stop(self) -> None:
        """Signal the polling loop and active bot to shut down and unload the LLM model."""
        self._stop_event.set()
        with self._state_lock:
            bot = self._bot
        if bot is not None:
            try:
                bot.stop()
            except Exception:
                pass
        try:
            get_workload_monitor().unload_model()
        except Exception:
            pass

    def get_status(self) -> Dict[str, Any]:
        """Return sanitized runtime check status fields for API consumers."""
        with self._state_lock:
            running = self._is_checking or self._check_lock.locked()
            return {
                "running": running,
                "last_check": self._last_check_at,
                "last_error": self._last_error,
            }


def serve_wsgi(app: Any, host: str, port: int, *, threads: int = 4) -> None:
    """Serve the Flask WSGI application using Waitress instead of Werkzeug's dev server.

    Args:
        app: WSGI application callable.
        host: Bind address.
        port: TCP port.
        threads: Worker thread count for concurrent requests.
    """
    logger.info("Serving iCal Bot Web UI via Waitress on http://%s:%s", host, port)
    waitress.serve(app, host=host, port=port, threads=threads, ident=None)


__all__ = ["BotRuntime", "serve_wsgi"]

