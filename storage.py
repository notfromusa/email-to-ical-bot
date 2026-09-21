"""SQLite-backed persistence layer for processed email results"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import config

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


class StorageManager:
    """Lightweight helper around SQLite for storing processed email results."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_results (
                    email_id TEXT PRIMARY KEY,
                    email_message_id TEXT,
                    email_subject TEXT NOT NULL,
                    email_sender TEXT,
                    email_recipients TEXT,
                    email_date TEXT,
                    email_body TEXT,
                    processed_at TEXT NOT NULL,
                    has_event INTEGER NOT NULL,
                    event_title TEXT,
                    event_description TEXT,
                    event_start TEXT,
                    event_end TEXT,
                    event_location TEXT,
                    event_candidates TEXT,
                    selected_event_index INTEGER,
                    llm_prompt TEXT,
                    llm_response TEXT,
                    llm_raw_result TEXT,
                    prompt_injection INTEGER NOT NULL DEFAULT 0,
                    ics_preview TEXT,
                    ics_content TEXT,
                    sent INTEGER NOT NULL DEFAULT 0,
                    dry_run INTEGER NOT NULL DEFAULT 0,
                    approval_required INTEGER NOT NULL DEFAULT 0,
                    approval_status TEXT,
                    approval_timestamp TEXT,
                    approved_by TEXT,
                    status TEXT,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT,
                    note TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY(email_id) REFERENCES processed_results(email_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_archive (
                    email_id TEXT PRIMARY KEY,
                    archived_at TEXT NOT NULL
                )
                """
            )
            self._ensure_columns(conn)
            conn.commit()
        self._repair_pending_states()
        self._reconcile_approval_statuses()

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        required_columns = {
            "email_message_id": "TEXT",
            "email_recipients": "TEXT",
            "email_body": "TEXT",
            "llm_prompt": "TEXT",
            "llm_response": "TEXT",
            "llm_raw_result": "TEXT",
            "event_candidates": "TEXT",
            "selected_event_index": "INTEGER",
            "prompt_injection": "INTEGER NOT NULL DEFAULT 0",
            "ics_content": "TEXT",
            "approval_required": "INTEGER NOT NULL DEFAULT 0",
            "approval_status": "TEXT",
            "approval_timestamp": "TEXT",
            "approved_by": "TEXT",
            "sent_event_indices": "TEXT",
        }
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info('processed_results')")
        }
        for column, definition in required_columns.items():
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE processed_results ADD COLUMN {column} {definition}"
                )

    def record_result(
        self,
        *,
        email_id: str,
        email_subject: str,
        email_sender: Optional[str],
        email_date: Optional[str],
        has_event: bool,
        event_data: Optional[Dict] = None,
        event_candidates: Optional[List[Dict[str, Any]]] = None,
        selected_event_index: Optional[int] = None,
        ics_preview: Optional[str] = None,
        ics_content: Optional[str] = None,
        sent: bool = False,
        dry_run: bool = False,
        status: Optional[str] = None,
        error: Optional[str] = None,
        email_message_id: Optional[str] = None,
        email_body: Optional[str] = None,
        llm_prompt: Optional[str] = None,
        llm_response: Optional[str] = None,
        llm_raw_result: Optional[Any] = None,
        prompt_injection: bool = False,
        email_recipients: Optional[Any] = None,
        approval_required: bool = False,
        approval_status: Optional[str] = None,
    ) -> None:
        """Insert or update a processed email result."""
        processed_at = datetime.utcnow().isoformat()
        if approval_required and not approval_status:
            approval_status = "pending"
        payload = {
            "email_id": email_id,
            "email_message_id": email_message_id,
            "email_subject": email_subject,
            "email_sender": email_sender,
            "email_recipients": self._serialize_raw(email_recipients),
            "email_date": email_date,
            "email_body": email_body,
            "processed_at": processed_at,
            "has_event": int(has_event),
            "event_title": None,
            "event_description": None,
            "event_start": None,
            "event_end": None,
            "event_location": None,
            "event_candidates": self._serialize_raw(event_candidates),
            "selected_event_index": selected_event_index,
            "llm_prompt": llm_prompt,
            "llm_response": llm_response,
            "llm_raw_result": self._serialize_raw(llm_raw_result),
            "prompt_injection": int(prompt_injection),
            "ics_preview": ics_preview,
            "ics_content": ics_content,
            "sent": int(sent),
            "dry_run": int(dry_run),
            "approval_required": int(approval_required),
            "approval_status": approval_status,
            "status": status,
            "error": error,
            "sent_event_indices": "[]",
        }

        if event_data:
            payload.update(
                {
                    "event_title": event_data.get("title"),
                    "event_description": event_data.get("description"),
                    "event_start": event_data.get("start_datetime"),
                    "event_end": event_data.get("end_datetime"),
                    "event_location": event_data.get("location"),
                }
            )

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO processed_results (
                    email_id, email_message_id, email_subject, email_sender, email_date,
                    email_recipients, email_body, processed_at, has_event, event_title, event_description,
                    event_start, event_end, event_location, event_candidates, selected_event_index,
                    llm_prompt, llm_response,
                    llm_raw_result, prompt_injection, ics_preview, ics_content, sent, dry_run,
                    approval_required, approval_status, status, error, sent_event_indices
                ) VALUES (
                    :email_id, :email_message_id, :email_subject, :email_sender, :email_date,
                    :email_recipients, :email_body, :processed_at, :has_event, :event_title, :event_description,
                    :event_start, :event_end, :event_location, :event_candidates, :selected_event_index,
                    :llm_prompt, :llm_response,
                    :llm_raw_result, :prompt_injection, :ics_preview, :ics_content, :sent, :dry_run,
                    :approval_required, :approval_status, :status, :error, :sent_event_indices
                )
                ON CONFLICT(email_id) DO UPDATE SET
                    email_message_id=excluded.email_message_id,
                    email_subject=excluded.email_subject,
                    email_sender=excluded.email_sender,
                    email_recipients=excluded.email_recipients,
                    email_date=excluded.email_date,
                    email_body=excluded.email_body,
                    processed_at=excluded.processed_at,
                    has_event=excluded.has_event,
                    event_title=excluded.event_title,
                    event_description=excluded.event_description,
                    event_start=excluded.event_start,
                    event_end=excluded.event_end,
                    event_location=excluded.event_location,
                    event_candidates=excluded.event_candidates,
                    selected_event_index=excluded.selected_event_index,
                    llm_prompt=excluded.llm_prompt,
                    llm_response=excluded.llm_response,
                    llm_raw_result=excluded.llm_raw_result,
                    prompt_injection=excluded.prompt_injection,
                    ics_preview=excluded.ics_preview,
                    ics_content=excluded.ics_content,
                    sent=excluded.sent,
                    dry_run=excluded.dry_run,
                    approval_required=excluded.approval_required,
                    approval_status=excluded.approval_status,
                    status=excluded.status,
                    error=excluded.error
                """,
                payload,
            )
            conn.commit()

    def get_results(self, filter_type: str = "all", limit: int = 200) -> List[Dict]:
        """Fetch processed results with optional filtering."""
        filter_map = {
            "events": "has_event = 1",
            "no_events": "has_event = 0",
            "pending": "approval_required = 1 AND (approval_status IS NULL OR approval_status = 'pending')",
        }
        clause = filter_map.get(filter_type)
        query = "SELECT * FROM processed_results"
        params: List[Any] = []
        if clause:
            query += f" WHERE {clause}"
        query += " ORDER BY datetime(processed_at) DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_result(self, email_id: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM processed_results WHERE email_id = ?", (email_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_archived_email_ids(self) -> Set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT email_id FROM processed_archive").fetchall()
        return {row[0] for row in rows}

    def mark_event_sent(
        self,
        email_id: str,
        *,
        status: str,
        sent: bool = True,
        approver: Optional[str] = None,
        error: Optional[str] = None,
        note: Optional[str] = None,
        event_data: Optional[Dict[str, Any]] = None,
        event_candidates: Optional[List[Dict[str, Any]]] = None,
        selected_event_index: Optional[int] = None,
        ics_content: Optional[str] = None,
        email_recipients: Optional[List[str]] = None,
        action: Optional[str] = None,
    ) -> None:
        timestamp = datetime.utcnow().isoformat()
        ics_preview = None
        if ics_content:
            ics_preview = ics_content[:500] + '...' if len(ics_content) > 500 else ics_content

        existing_sent_indices: List[int] = []
        raw_candidates = None
        with self._connect() as conn:
            existing_row = conn.execute(
                "SELECT sent_event_indices, event_candidates FROM processed_results WHERE email_id = ?",
                (email_id,),
            ).fetchone()
            if existing_row:
                if existing_row["sent_event_indices"]:
                    try:
                        existing_sent_indices = json.loads(existing_row["sent_event_indices"])
                        if not isinstance(existing_sent_indices, list):
                            existing_sent_indices = []
                    except Exception:
                        existing_sent_indices = []
                if not event_candidates and existing_row["event_candidates"]:
                    try:
                        raw_candidates = json.loads(existing_row["event_candidates"])
                    except Exception:
                        pass

        candidates_list = event_candidates if event_candidates is not None else (raw_candidates or [])
        total_candidates = len(candidates_list) if isinstance(candidates_list, list) and candidates_list else 1

        if (sent or status == 'dry_run_preview') and selected_event_index is not None:
            if selected_event_index not in existing_sent_indices:
                existing_sent_indices.append(selected_event_index)
            existing_sent_indices.sort()

        all_sent = len(existing_sent_indices) >= total_candidates if total_candidates > 1 else bool(sent or status == 'dry_run_preview')

        if not (sent or status == 'dry_run_preview'):
            effective_sent = False
            effective_approval_status = "pending"
            effective_status = status
        else:
            effective_sent = sent and all_sent
            effective_approval_status = "approved" if all_sent else "pending"
            if total_candidates > 1 and not all_sent:
                effective_status = f"partially_sent ({len(existing_sent_indices)}/{total_candidates})"
            else:
                effective_status = status

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE processed_results
                SET sent = :sent,
                    status = :status,
                    sent_event_indices = :sent_event_indices,
                    approval_status = CASE
                        WHEN approval_required = 1 THEN :approval_status
                        ELSE approval_status
                    END,
                    approval_timestamp = CASE
                        WHEN approval_required = 1 THEN :approval_timestamp
                        ELSE approval_timestamp
                    END,
                    approved_by = CASE
                        WHEN approval_required = 1 THEN :approved_by
                        ELSE approved_by
                    END,
                    event_title = COALESCE(:event_title, event_title),
                    event_description = COALESCE(:event_description, event_description),
                    event_start = COALESCE(:event_start, event_start),
                    event_end = COALESCE(:event_end, event_end),
                    event_location = COALESCE(:event_location, event_location),
                    event_candidates = COALESCE(:event_candidates, event_candidates),
                    selected_event_index = COALESCE(:selected_event_index, selected_event_index),
                    email_recipients = COALESCE(:email_recipients, email_recipients),
                    ics_content = COALESCE(:ics_content, ics_content),
                    ics_preview = COALESCE(:ics_preview, ics_preview),
                    error = :error
                WHERE email_id = :email_id
                """,
                {
                    "sent": int(effective_sent),
                    "status": effective_status,
                    "sent_event_indices": json.dumps(existing_sent_indices),
                    "approval_status": effective_approval_status,
                    "approval_timestamp": timestamp if (sent or status == 'dry_run_preview') else None,
                    "approved_by": approver,
                    "event_title": event_data.get('title') if event_data else None,
                    "event_description": event_data.get('description') if event_data else None,
                    "event_start": event_data.get('start_datetime') if event_data else None,
                    "event_end": event_data.get('end_datetime') if event_data else None,
                    "event_location": event_data.get('location') if event_data else None,
                    "event_candidates": self._serialize_raw(event_candidates),
                    "selected_event_index": selected_event_index,
                    "email_recipients": json.dumps(email_recipients) if email_recipients else None,
                    "ics_content": ics_content,
                    "ics_preview": ics_preview,
                    "error": error,
                    "email_id": email_id,
                },
            )
            conn.commit()

        if sent or status == 'dry_run_preview':
            effective_action = action or ('approved' if sent else 'dry_run_preview')
            cand_prefix = f"[Candidate {selected_event_index + 1}/{total_candidates}] " if selected_event_index is not None and total_candidates > 1 else ""
            log_note = f"{cand_prefix}{note or ''}".strip() or None
        else:
            effective_action = action or 'send_error'
            log_note = error or note
        self.log_approval_action(email_id, effective_action, actor=approver, note=log_note)

    def reset_for_rescan(self, email_id: str) -> None:
        """Reset an email's approval and sent state so it can be re-evaluated cleanly."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE processed_results
                SET sent = 0,
                    sent_event_indices = '[]',
                    approval_status = 'pending',
                    status = 'pending_approval',
                    approval_timestamp = NULL,
                    approved_by = NULL,
                    error = NULL
                WHERE email_id = ?
                """,
                (email_id,),
            )
            conn.commit()

    def get_stats(self) -> Dict:
        """Compute statistics for dashboard consumption."""
        with self._connect() as conn:
            active_total = conn.execute("SELECT COUNT(*) FROM processed_results").fetchone()[0]
            events = conn.execute(
                "SELECT COUNT(*) FROM processed_results WHERE has_event = 1"
            ).fetchone()[0]
            sent_events = conn.execute(
                "SELECT COUNT(*) FROM processed_results WHERE has_event = 1 AND sent = 1"
            ).fetchone()[0]
            dry_run_events = conn.execute(
                "SELECT COUNT(*) FROM processed_results WHERE has_event = 1 AND dry_run = 1"
            ).fetchone()[0]
            pending = conn.execute(
                """
                SELECT COUNT(*) FROM processed_results
                WHERE approval_required = 1 AND (approval_status IS NULL OR approval_status = 'pending')
                """
            ).fetchone()[0]
            last_processed = conn.execute(
                "SELECT processed_at FROM processed_results ORDER BY datetime(processed_at) DESC LIMIT 1"
            ).fetchone()
            event_rows = conn.execute(
                "SELECT event_start FROM processed_results WHERE has_event = 1 AND event_start IS NOT NULL"
            ).fetchall()
            archived_total = conn.execute(
                "SELECT COUNT(*) FROM processed_archive"
            ).fetchone()[0]

        total_processed = active_total + archived_total
        detection_rate = float(events) / active_total if active_total else 0.0
        now = datetime.now(timezone.utc)
        upcoming = 0
        past = 0
        for row in event_rows:
            try:
                event_start = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
                if event_start >= now:
                    upcoming += 1
                else:
                    past += 1
            except Exception:
                continue

        metrics = self.get_database_metrics(total_hint=total_processed)

        return {
            "total_processed": total_processed,
            "events_detected": events,
            "no_event_count": active_total - events,
            "sent_events": sent_events,
            "dry_run_events": dry_run_events,
            "pending_approvals": pending,
            "detection_rate": detection_rate,
            "upcoming_events": upcoming,
            "past_events": past,
            "last_processed_at": last_processed[0] if last_processed else None,
            "db_entries": metrics["entries"],
            "db_size_bytes": metrics["file_size_bytes"],
            "archived_records": archived_total,
        }

    def get_database_metrics(self, total_hint: Optional[int] = None) -> Dict:
        """Return entry count and file size for the SQLite database."""
        if total_hint is None:
            with self._connect() as conn:
                total = conn.execute("SELECT COUNT(*) FROM processed_results").fetchone()[0]
        else:
            total = total_hint

        size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {"entries": total, "file_size_bytes": size}

    def censor_sensitive_fields(self) -> Dict[str, int]:
        """Remove stored email bodies and heavy LLM context payloads."""
        stats = {
            "records_scanned": 0,
            "records_updated": 0,
            "email_body_cleared": 0,
            "prompts_redacted": 0,
            "contexts_cleared": 0,
        }

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT email_id, email_body, llm_prompt, llm_raw_result FROM processed_results"
            ).fetchall()
            stats["records_scanned"] = len(rows)

            for row in rows:
                updates: Dict[str, Any] = {}

                if row["email_body"]:
                    updates["email_body"] = None
                    stats["email_body_cleared"] += 1

                redacted_prompt = self._redact_prompt_body(row["llm_prompt"])
                if redacted_prompt is not None and redacted_prompt != row["llm_prompt"]:
                    updates["llm_prompt"] = redacted_prompt
                    stats["prompts_redacted"] += 1

                cleaned_raw = self._strip_context_payload(row["llm_raw_result"])
                if cleaned_raw is not None and cleaned_raw != row["llm_raw_result"]:
                    updates["llm_raw_result"] = cleaned_raw
                    stats["contexts_cleared"] += 1

                if updates:
                    updates["email_id"] = row["email_id"]
                    set_clause = ", ".join(
                        f"{column} = :{column}" for column in updates.keys() if column != "email_id"
                    )
                    conn.execute(
                        f"UPDATE processed_results SET {set_clause} WHERE email_id = :email_id",
                        updates,
                    )
                    stats["records_updated"] += 1

            conn.commit()

        return stats

    def _repair_pending_states(self) -> None:
        """Ensure pending approvals remain marked and logged across restarts."""
        pending_ids: List[str] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT email_id FROM processed_results
                WHERE approval_required = 1
                  AND sent = 0
                  AND (approval_status IS NULL OR approval_status = '')
                """
            ).fetchall()
            pending_ids = [row[0] for row in rows]
            if pending_ids:
                conn.executemany(
                    "UPDATE processed_results SET approval_status = 'pending' WHERE email_id = ?",
                    [(email_id,) for email_id in pending_ids],
                )
                conn.commit()

        all_pending_ids: List[str] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT email_id FROM processed_results
                WHERE approval_required = 1
                  AND sent = 0
                  AND approval_status = 'pending'
                """
            ).fetchall()
            all_pending_ids = [row[0] for row in rows]

        for email_id in all_pending_ids:
            self.log_approval_action(email_id, 'pending')

    def _reconcile_approval_statuses(self) -> int:
        """Normalize approval_status based on latest logs/status flags."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT email_id, approval_status, status, sent
                FROM processed_results
                WHERE approval_required = 1
                """
            ).fetchall()

        updates = []
        for row in rows:
            email_id = row["email_id"]
            status = (row["status"] or "").lower()
            sent = bool(row["sent"])
            current = (row["approval_status"] or "").lower()

            normalized = current
            if sent or status in {"invite_sent", "approved"}:
                normalized = "approved"
            elif status == "declined":
                normalized = "declined"
            elif status == "dry_run_preview":
                normalized = "dry_run"
            elif status == "send_error":
                normalized = "pending"
            elif status == "pending_approval":
                normalized = "pending"

            if not normalized or normalized == "pending":
                with self._connect() as conn:
                    last = conn.execute(
                        """
                        SELECT action
                        FROM approval_logs
                        WHERE email_id = ?
                        ORDER BY datetime(timestamp) DESC
                        LIMIT 1
                        """,
                        (email_id,),
                    ).fetchone()
                if last:
                    action = (last[0] or "").lower()
                    if action in {"approved", "declined", "dry_run_preview"}:
                        normalized = "dry_run" if action == "dry_run_preview" else action
                    elif action == "send_error":
                        normalized = "pending"

            if normalized and normalized != current:
                updates.append((normalized, email_id))

        if updates:
            with self._connect() as conn:
                conn.executemany(
                    "UPDATE processed_results SET approval_status = ? WHERE email_id = ?",
                    updates,
                )
                conn.commit()
        return len(updates)

    def reconcile_approvals(self) -> Dict[str, Any]:
        """Public repair entry point for approval_status inconsistencies."""
        updated = self._reconcile_approval_statuses()
        return {
            "updated": updated,
        }

    def purge_old_entries(self, months: int = 2, days: Optional[int] = None) -> Dict[str, int]:
        """Remove stale records older than `months` months or explicit `days`."""
        if days is not None:
            days = max(int(days), 1)
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        else:
            months = max(months, 1)
            cutoff = datetime.now(timezone.utc) - timedelta(days=30 * months)
        stats = {
            "cutoff_iso": cutoff.isoformat(),
            "records_scanned": 0,
            "removed_no_event": 0,
            "removed_event": 0,
            "records_deleted": 0,
        }

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT email_id, has_event, email_date, processed_at, event_start, event_end
                FROM processed_results
                """
            ).fetchall()

        stats["records_scanned"] = len(rows)
        removable_ids = []

        for row in rows:
            has_event = bool(row["has_event"])
            if has_event:
                event_dt = self._parse_iso_datetime(row["event_end"]) or self._parse_iso_datetime(row["event_start"])
                if event_dt and event_dt < cutoff:
                    removable_ids.append(row["email_id"])
                    stats["removed_event"] += 1
            else:
                ref_dt = self._parse_iso_datetime(row["email_date"]) or self._parse_iso_datetime(row["processed_at"])
                if ref_dt and ref_dt < cutoff:
                    removable_ids.append(row["email_id"])
                    stats["removed_no_event"] += 1

        if removable_ids:
            self._archive_email_ids(removable_ids)
            with self._connect() as conn:
                conn.executemany(
                    "DELETE FROM processed_results WHERE email_id = ?",
                    [(email_id,) for email_id in removable_ids],
                )
                conn.commit()
            stats["records_deleted"] = len(removable_ids)

        return stats

    def _archive_email_ids(self, email_ids: List[str]) -> None:
        if not email_ids:
            return
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO processed_archive (email_id, archived_at)
                VALUES (?, ?)
                ON CONFLICT(email_id) DO NOTHING
                """,
                [(email_id, timestamp) for email_id in email_ids],
            )
            conn.commit()

    @staticmethod
    def _serialize_raw(raw: Optional[Any]) -> Optional[str]:
        if raw is None:
            return None
        if isinstance(raw, str):
            return raw
        try:
            return json.dumps(raw, ensure_ascii=False)
        except TypeError:
            return str(raw)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict:
        return {key: row[key] for key in row.keys()}

    @staticmethod
    def _default_timezone():
        timezone_name = (getattr(config, 'DEFAULT_TIMEZONE', '') or '').strip()
        if not timezone_name or timezone_name.upper() == 'UTC':
            timezone_name = 'Europe/Berlin'
        if ZoneInfo is None:
            return timezone.utc
        try:
            return ZoneInfo(timezone_name)
        except Exception:
            return timezone.utc

    @staticmethod
    def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=StorageManager._default_timezone())
        return dt

    @staticmethod
    def _redact_prompt_body(prompt: Optional[str]) -> Optional[str]:
        if not prompt or "Email Body:" not in prompt:
            return prompt
        marker = "Email Body:"
        start_idx = prompt.find(marker)
        task_idx = prompt.find("\n\nTask:", start_idx)
        if start_idx == -1 or task_idx == -1:
            return prompt
        prefix = prompt[: start_idx + len(marker)]
        suffix = prompt[task_idx:]
        redacted_body = "\n[REDACTED]"
        if not suffix.startswith("\n"):
            suffix = "\n" + suffix
        return f"{prefix}{redacted_body}{suffix}"

    @staticmethod
    def _strip_context_payload(raw_value: Optional[Any]) -> Optional[Any]:
        if raw_value is None:
            return raw_value
        if isinstance(raw_value, bytes):
            try:
                raw_text = raw_value.decode("utf-8")
            except UnicodeDecodeError:
                return raw_value
        else:
            raw_text = raw_value

        if not isinstance(raw_text, str):
            return raw_value

        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return raw_value

        if not isinstance(payload, dict) or not payload.get("context"):
            return raw_value

        payload["context"] = []
        return json.dumps(payload, ensure_ascii=False)

    def log_approval_action(
        self,
        email_id: str,
        action: str,
        *,
        actor: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        if not email_id or not action:
            return
        timestamp = datetime.utcnow().isoformat()
        with self._connect() as conn:
            if action == 'pending':
                exists = conn.execute(
                    "SELECT 1 FROM approval_logs WHERE email_id = ? AND action = 'pending'",
                    (email_id,),
                ).fetchone()
                if exists:
                    return
            conn.execute(
                """
                INSERT INTO approval_logs (email_id, action, actor, note, timestamp)
                VALUES (:email_id, :action, :actor, :note, :timestamp)
                """,
                {
                    "email_id": email_id,
                    "action": action,
                    "actor": actor,
                    "note": note,
                    "timestamp": timestamp,
                },
            )
            conn.commit()

    def get_approval_history(self, email_id: str) -> List[Dict[str, Any]]:
        if not email_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT action, actor, note, timestamp
                FROM approval_logs
                WHERE email_id = ?
                ORDER BY datetime(timestamp) ASC
                """,
                (email_id,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def mark_event_declined(
        self,
        email_id: str,
        *,
        actor: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        timestamp = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE processed_results
                SET approval_status = 'declined',
                    status = 'declined',
                    approval_timestamp = :approval_timestamp,
                    approved_by = :approved_by,
                    sent = 0
                WHERE email_id = :email_id
                """,
                {
                    "email_id": email_id,
                    "approval_timestamp": timestamp,
                    "approved_by": actor,
                },
            )
            conn.commit()

        self.log_approval_action(email_id, 'declined', actor=actor, note=note)
