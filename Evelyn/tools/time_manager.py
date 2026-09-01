# time_manager.py
# date created: 2026-08-29
# date modified: 2026-09-01 17:29:20
# tags: #temporal, #time-manager, #agenda, #heartbeat, #scheduling

"""time_manager.py — Evelyn Temporal Management Subsystem.

Encapsulates idle gap evaluation, absolute chronology, schema-adaptive agenda lookups,
XML temporal envelope construction, and proactive heartbeat tick evaluation for
autonomous operations.
"""

import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import evelyn_config as cfg


def get_user_idle_seconds(db_path: str | None = None) -> float:
    """Calculate elapsed seconds of silence since the last user message in chat history.

    Args:
        db_path: Optional explicit chat DB path. Defaults to cfg.CHAT_DB_PATH.

    Returns:
        float: Elapsed seconds since the latest user message, or a large fallback (999999.0)
        if no user messages exist or the database cannot be queried.
    """
    target_path = db_path or getattr(cfg, "CHAT_DB_PATH", os.path.join(getattr(cfg, "DATA_DIR", ""), "evelyn_chat.db"))
    if not os.path.exists(target_path):
        return 999999.0

    try:
        con = sqlite3.connect(target_path)
        row = con.execute("SELECT MAX(ts) FROM messages WHERE role = 'user'").fetchone()
        con.close()
        if row and row[0] is not None:
            return max(0.0, time.time() - float(row[0]))
    except (sqlite3.Error, OSError):
        pass
    return 999999.0


class TimeManager:
    """Manages conversational timing, idle gap detection, agenda tracking, and heartbeat triggers."""

    def __init__(
        self,
        idle_threshold_minutes: int = 45,
        calendar_lookahead_hours: int = 4,
        task_lookahead_hours: int = 2,
        timezone_name: str | None = None,
    ) -> None:
        """Initialize the TimeManager subsystem.

        Args:
            idle_threshold_minutes: Silence threshold in minutes before declaring a resumed session gap (default: 45m).
            calendar_lookahead_hours: Hours ahead to inspect upcoming calendar events.
            task_lookahead_hours: Hours ahead to inspect pending or imminent tasks.
            timezone_name: Timezone string identifier (defaults to cfg.USER_TIMEZONE).
        """
        self.idle_threshold_minutes = idle_threshold_minutes
        self.calendar_lookahead_hours = calendar_lookahead_hours
        self.task_lookahead_hours = task_lookahead_hours
        self.tz_name = timezone_name or getattr(cfg, "USER_TIMEZONE", "America/Chicago")
        try:
            self.local_tz = ZoneInfo(self.tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            self.local_tz = datetime.now().astimezone().tzinfo or UTC

        # Alert deduplication cache: stores (type, entity_id, milestone, event_dt_iso)
        self._fired_alerts: set[tuple[str, str, str, str]] = set()

    def parse_dt(self, val: Any) -> datetime | None:
        """Normalize varied database date/time representations to an aware datetime in local timezone.

        Handles:
        - datetime objects (ensures aware in local_tz)
        - UNIX epoch floats/ints (from messages.ts)
        - All-day date-only strings 'YYYY-MM-DD' (anchored to midnight in local_tz)
        - ISO 8601 / RFC 3339 strings (with 'Z' or offset)
        - Standard SQLite timestamp strings ('YYYY-MM-DD HH:MM:SS')

        Args:
            val: Raw date/time representation.

        Returns:
            datetime | None: Timezone-aware datetime in local timezone, or None if invalid.
        """
        if isinstance(val, datetime):
            if val.tzinfo is None:
                return val.replace(tzinfo=self.local_tz)
            return val.astimezone(self.local_tz)

        if not val or not isinstance(val, (str, int, float)):
            return None

        if isinstance(val, (int, float)):
            try:
                return datetime.fromtimestamp(val, tz=UTC).astimezone(self.local_tz)
            except (OSError, OverflowError, ValueError):
                return None

        clean = val.strip() if isinstance(val, str) else str(val).strip()
        if not clean:
            return None

        # 1. Date-only string (e.g., all-day calendar event 'YYYY-MM-DD' or 'YYYY/MM/DD')
        if len(clean) == 10 and (clean.count("-") == 2 or clean.count("/") == 2):
            fmt = "%Y-%m-%d" if "-" in clean else "%Y/%m/%d"
            try:
                naive = datetime.strptime(clean, fmt)  # noqa: DTZ007
                return naive.replace(tzinfo=self.local_tz)
            except ValueError:
                pass

        # 2. ISO 8601 / RFC 3339 string (e.g., '2026-08-29T10:00:00-05:00' or '2026-08-29T15:00:00.000Z')
        if "T" in clean:
            try:
                dt = datetime.fromisoformat(clean.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=self.local_tz)
                return dt.astimezone(self.local_tz)
            except ValueError:
                pass

        # 3. Standard SQLite timestamp formats
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
            try:
                naive = datetime.strptime(clean, fmt)  # noqa: DTZ007
                return naive.replace(tzinfo=self.local_tz)
            except ValueError:
                continue

        return None

    def get_last_interaction_ts(self, con: sqlite3.Connection) -> datetime | None:
        """Query the timestamp of the latest message in chat history regardless of role.

        Resolves role blindness by measuring silence against the most recent turn.

        Args:
            con: SQLite database connection.

        Returns:
            datetime | None: Timezone-aware datetime of the last message, or None.
        """
        try:
            row = con.execute("SELECT ts FROM messages ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                ts_val = row[0] if isinstance(row, (tuple, list)) else row["ts"]
                return self.parse_dt(ts_val)
        except (sqlite3.Error, KeyError, IndexError):
            pass
        return None

    def evaluate_session_gap(
        self, con: sqlite3.Connection, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """Determine whether the conversation was idle for longer than idle_threshold_minutes.

        Args:
            con: SQLite database connection.
            now: Optional current datetime (defaults to now in local_tz).

        Returns:
            dict | None: Dictionary with elapsed duration metadata if gap >= threshold, else None.
        """
        current_now = now or datetime.now(self.local_tz)
        if current_now.tzinfo is None:
            current_now = current_now.replace(tzinfo=self.local_tz)

        last_ts = self.get_last_interaction_ts(con)
        if not last_ts:
            return None  # First conversation or empty history

        elapsed = current_now - last_ts
        elapsed_minutes = int(elapsed.total_seconds() // 60)

        if elapsed_minutes < self.idle_threshold_minutes:
            return None

        hours, minutes = divmod(elapsed_minutes, 60)
        days, hours = divmod(hours, 24)

        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes or not parts:
            parts.append(f"{minutes}m")

        return {
            "elapsed_minutes": elapsed_minutes,
            "duration_str": " ".join(parts),
            "last_interaction_ts": last_ts.strftime("%Y-%m-%d %I:%M %p"),
        }

    def get_calendar_agenda(
        self, con: sqlite3.Connection, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Extract upcoming and currently active calendar events within the lookahead window.

        Args:
            con: SQLite database connection.
            now: Optional current datetime (defaults to now in local_tz).

        Returns:
            list[dict]: List of event metadata dictionaries with relative timing descriptions.
        """
        current_now = now or datetime.now(self.local_tz)
        if current_now.tzinfo is None:
            current_now = current_now.replace(tzinfo=self.local_tz)

        window_end = current_now + timedelta(hours=self.calendar_lookahead_hours)
        events = []

        try:
            # Query events across today and lookahead window
            today_date_str = current_now.strftime("%Y-%m-%d")
            window_end_iso = window_end.isoformat()

            rows = con.execute(
                """
                SELECT id, summary, description, start_at, end_at, location
                FROM calendar_events
                WHERE (start_at <= ? AND (end_at >= ? OR end_at >= ?))
                   OR (start_at >= ? AND start_at <= ?)
                   OR (start_at = ?)
                ORDER BY start_at ASC
                """,
                (
                    window_end_iso,
                    current_now.isoformat(),
                    today_date_str,
                    current_now.isoformat(),
                    window_end_iso,
                    today_date_str,
                ),
            ).fetchall()

            for row in rows:
                ev_id = row[0] if isinstance(row, (tuple, list)) else row["id"]
                summary = row[1] if isinstance(row, (tuple, list)) else row["summary"]
                start_raw = row[3] if isinstance(row, (tuple, list)) else row["start_at"]
                end_raw = row[4] if isinstance(row, (tuple, list)) else row["end_at"]

                start_dt = self.parse_dt(start_raw)
                end_dt = self.parse_dt(end_raw)

                if not start_dt:
                    continue

                # Check if all-day event
                is_all_day = len(str(start_raw).strip()) == 10 and "-" in str(start_raw)
                if is_all_day:
                    if start_dt.date() == current_now.date():
                        status = "All day today"
                    else:
                        status = f"All day on {start_dt.strftime('%a %b %d')}"
                    diff_minutes = int((start_dt - current_now).total_seconds() // 60)
                    start_str = "All day"
                else:
                    diff_minutes = int((start_dt - current_now).total_seconds() // 60)
                    if diff_minutes < 0:
                        if end_dt and end_dt > current_now:
                            status = f"In progress (started {abs(diff_minutes)}m ago)"
                        else:
                            status = f"Ended {abs(diff_minutes)}m ago"
                    elif diff_minutes == 0:
                        status = "Starting now"
                    elif diff_minutes < 60:
                        status = f"In {diff_minutes} minutes"
                    else:
                        status = f"In {diff_minutes // 60}h {diff_minutes % 60}m"
                    start_str = start_dt.strftime("%I:%M %p").lstrip("0")

                events.append(
                    {
                        "id": str(ev_id),
                        "title": summary or "Untitled Event",
                        "status": status,
                        "diff_minutes": diff_minutes,
                        "start_str": start_str,
                        "start_dt": start_dt,
                        "is_all_day": is_all_day,
                    }
                )
        except sqlite3.OperationalError:
            pass

        return events

    def get_imminent_tasks(
        self, con: sqlite3.Connection, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Extract overdue or upcoming pending tasks within the lookahead window.

        Args:
            con: SQLite database connection.
            now: Optional current datetime (defaults to now in local_tz).

        Returns:
            list[dict]: List of task metadata dictionaries with relative timing descriptions.
        """
        current_now = now or datetime.now(self.local_tz)
        if current_now.tzinfo is None:
            current_now = current_now.replace(tzinfo=self.local_tz)

        window_end = current_now + timedelta(hours=self.task_lookahead_hours)
        tasks = []

        try:
            rows = con.execute(
                """
                SELECT id, title, due_at, status, notes
                FROM tasks
                WHERE status != 'completed'
                  AND due_at IS NOT NULL
                  AND due_at != ''
                ORDER BY due_at ASC
                """
            ).fetchall()

            for row in rows:
                task_id = row[0] if isinstance(row, (tuple, list)) else row["id"]
                title = row[1] if isinstance(row, (tuple, list)) else row["title"]
                due_raw = row[2] if isinstance(row, (tuple, list)) else row["due_at"]

                due_dt = self.parse_dt(due_raw)
                if not due_dt:
                    continue

                if due_dt > window_end:
                    continue  # Beyond lookahead window

                diff_minutes = int((due_dt - current_now).total_seconds() // 60)
                if diff_minutes < 0:
                    status = f"Overdue by {abs(diff_minutes)}m"
                elif diff_minutes == 0:
                    status = "Due right now"
                elif diff_minutes < 60:
                    status = f"Due in {diff_minutes} minutes"
                else:
                    status = f"Due in {diff_minutes // 60}h {diff_minutes % 60}m"

                tasks.append(
                    {
                        "id": str(task_id),
                        "title": title or "Untitled Task",
                        "status": status,
                        "diff_minutes": diff_minutes,
                        "due_dt": due_dt,
                        "due_str": due_dt.strftime("%I:%M %p").lstrip("0") if "T" in str(due_raw) else due_dt.strftime("%b %d"),
                    }
                )
        except sqlite3.OperationalError:
            pass

        return tasks

    def build_temporal_envelope(
        self, con: sqlite3.Connection, now: datetime | None = None
    ) -> str:
        """Construct unambiguous XML environmental telemetry metadata for model ingestion.

        Args:
            con: SQLite database connection.
            now: Optional current datetime (defaults to now in local_tz).

        Returns:
            str: Structured XML temporal envelope block.
        """
        current_now = now or datetime.now(self.local_tz)
        if current_now.tzinfo is None:
            current_now = current_now.replace(tzinfo=self.local_tz)

        gap = self.evaluate_session_gap(con, current_now)
        events = self.get_calendar_agenda(con, current_now)
        tasks = self.get_imminent_tasks(con, current_now)
        time_str = current_now.strftime("%A, %b %d, %Y, %I:%M %p %Z").replace(" 0", " ")

        try:
            from Evelyn.tools.string_utils import build_temporal_envelope as _build_envelope
        except ImportError:
            from string_utils import build_temporal_envelope as _build_envelope

        return _build_envelope(
            current_time=time_str,
            session_gap=gap,
            calendar_events=events,
            task_events=tasks,
        )

    def evaluate_heartbeat(
        self, con: sqlite3.Connection, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Evaluate time thresholds for autonomous wake cycles and alert triggers.

        Includes deduplication caching and automatic TTL pruning of stale alert entries.

        Args:
            con: SQLite database connection.
            now: Optional current datetime (defaults to now in local_tz).

        Returns:
            list[dict]: List of newly triggered autonomous alert dictionaries.
        """
        current_now = now or datetime.now(self.local_tz)
        if current_now.tzinfo is None:
            current_now = current_now.replace(tzinfo=self.local_tz)

        triggers = []
        active_cache_keys: set[tuple[str, str, str, str]] = set()

        # 1. Evaluate imminent and overdue tasks
        for task in self.get_imminent_tasks(con, current_now):
            task_id = task["id"]
            diff = task["diff_minutes"]
            due_iso = task["due_dt"].isoformat()

            if diff <= 0:
                key = ("task", task_id, "overdue", due_iso)
                active_cache_keys.add(key)
                if key not in self._fired_alerts:
                    self._fired_alerts.add(key)
                    triggers.append(
                        {
                            "type": "task_overdue",
                            "entity_id": task_id,
                            "title": task["title"],
                            "details": f"Task '{task['title']}' is now overdue ({task['status']}).",
                        }
                    )
            elif 0 < diff <= 15:
                key = ("task", task_id, "imminent_15", due_iso)
                active_cache_keys.add(key)
                if key not in self._fired_alerts:
                    self._fired_alerts.add(key)
                    triggers.append(
                        {
                            "type": "task_imminent",
                            "entity_id": task_id,
                            "title": task["title"],
                            "details": f"Task '{task['title']}' is due in {diff} minutes.",
                        }
                    )

        # 2. Evaluate imminent calendar events
        for ev in self.get_calendar_agenda(con, current_now):
            if ev.get("is_all_day"):
                continue  # All-day events do not trigger minute-level threshold alerts

            ev_id = ev["id"]
            diff = ev["diff_minutes"]
            start_iso = ev["start_dt"].isoformat()

            if 0 <= diff <= 10:
                key = ("event", ev_id, "start_10", start_iso)
                active_cache_keys.add(key)
                if key not in self._fired_alerts:
                    self._fired_alerts.add(key)
                    triggers.append(
                        {
                            "type": "event_imminent",
                            "entity_id": ev_id,
                            "title": ev["title"],
                            "details": f"Calendar event '{ev['title']}' starts at {ev['start_str']} ({ev['status']}).",
                        }
                    )

        # 3. Prune stale cache entries older than lookahead window (TTL pruning)
        prune_cutoff = current_now - timedelta(hours=max(self.calendar_lookahead_hours, self.task_lookahead_hours))
        stale_keys = set()
        for alert in self._fired_alerts:
            _type, _id, _milestone, dt_str = alert
            parsed_dt = self.parse_dt(dt_str)
            if parsed_dt and parsed_dt < prune_cutoff:
                stale_keys.add(alert)

        self._fired_alerts.difference_update(stale_keys)
        return triggers

    def reset_alert_cache(self) -> None:
        """Clear the alert deduplication cache."""
        self._fired_alerts.clear()
