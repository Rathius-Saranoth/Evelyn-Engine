# reminders.py
# date created: 2026-06-19
# date modified: 2026-06-21
# tags: #reminders, #agenda, #tasks, #gcal, #offline-first

"""reminders.py — Local Reminders Management & Unified Agenda Aggregator.

Provides local reminder CRUD operations, and compiles local reminders alongside
cached Google Calendar events into a single chronological agenda.

Recurrence patterns (recurrence_rule column):
  'daily'        — advance due_at by one day on each completion.
  'weekly:MON'   — advance to the same weekday next week (MON/TUE/WED/THU/FRI/SAT/SUN).
  'monthly:15'   — advance to the same day-of-month next month (1–28).
  None / ''      — one-shot; mark completed as usual.
"""

import sqlite3
import datetime
import evelyn_config as cfg
from gcal_sync import get_cached_gcal_events

def _next_due(recurrence_rule: str, current_due: str) -> str | None:
    """Calculate the next due datetime for a recurring reminder.

    Supports three simple patterns that cover the vast majority of real-world
    recurring reminders without requiring a third-party cron library.

    Args:
        recurrence_rule: One of 'daily', 'weekly:MON', or 'monthly:15'.
                         Case-insensitive for the weekday suffix.
        current_due: The current due timestamp as 'YYYY-MM-DD HH:MM:SS'.

    Returns:
        str | None: Next due timestamp as 'YYYY-MM-DD HH:MM:SS', or None if
                    the rule is unrecognised or the current_due is unparseable.
    """
    _WEEKDAY_MAP = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
    try:
        dt = datetime.datetime.strptime(current_due.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        # Try date-only fallback (normalise to midnight)
        try:
            dt = datetime.datetime.strptime(current_due.strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None

    rule = (recurrence_rule or "").strip().lower()

    if rule == "daily":
        next_dt = dt + datetime.timedelta(days=1)

    elif rule.startswith("weekly:"):
        day_str = rule.split(":", 1)[1].upper()
        target_wd = _WEEKDAY_MAP.get(day_str)
        if target_wd is None:
            return None
        days_ahead = (target_wd - dt.weekday()) % 7
        # Always advance at least one day to avoid same-day repeat
        if days_ahead == 0:
            days_ahead = 7
        next_dt = dt + datetime.timedelta(days=days_ahead)

    elif rule.startswith("monthly:"):
        try:
            target_day = int(rule.split(":", 1)[1])
        except ValueError:
            return None
        target_day = max(1, min(target_day, 28))  # Clamp to safe range
        # Advance to next month, keeping the same time
        if dt.month == 12:
            next_dt = dt.replace(year=dt.year + 1, month=1, day=target_day)
        else:
            next_dt = dt.replace(month=dt.month + 1, day=target_day)

    else:
        return None  # Unknown rule — caller treats as one-shot

    return next_dt.strftime("%Y-%m-%d %H:%M:%S")


def create_reminder(title: str, due_at: str, description: str = None, recurrence_rule: str = None) -> dict:
    """Create a new local reminder in the SQLite database.

    Args:
        title: Short title of the reminder.
        due_at: Due timestamp (ISO-8601 string, 'YYYY-MM-DD HH:MM:SS').
        description: Optional detailed notes.
        recurrence_rule: Optional recurrence pattern ('daily', 'weekly:MON', 'monthly:15').
                         Pass None for a one-shot reminder.

    Returns:
        dict: The created reminder.
    """
    con = sqlite3.connect(cfg.CHAT_DB_PATH)
    con.row_factory = sqlite3.Row
    
    created_at = datetime.datetime.utcnow().isoformat()

    # Simple normalization of due_at if user/model passed standard spacing
    due_at = due_at.replace("T", " ")

    # Normalize recurrence_rule to None if blank
    recurrence_rule = recurrence_rule.strip() if recurrence_rule and recurrence_rule.strip() else None

    cur = con.execute(
        """
        INSERT INTO reminders (title, description, due_at, status, created_at, notified, recurrence_rule)
        VALUES (?, ?, ?, 'pending', ?, 0, ?)
        """,
        (title, description, due_at, created_at, recurrence_rule)
    )
    reminder_id = cur.lastrowid
    con.commit()
    
    row = con.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    reminder = dict(row)
    con.close()
    
    return reminder

def list_reminders(status: str = "pending", limit: int = 50) -> list:
    """List local reminders filtered by status.

    Args:
        status: Status filter ('pending', 'completed', 'dismissed', or 'all').
        limit: Max number of reminders to return.

    Returns:
        list: List of reminders.
    """
    con = sqlite3.connect(cfg.CHAT_DB_PATH)
    con.row_factory = sqlite3.Row
    
    if status == "all":
        rows = con.execute(
            "SELECT * FROM reminders ORDER BY due_at ASC LIMIT ?", (limit,)
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM reminders WHERE status = ? ORDER BY due_at ASC LIMIT ?",
            (status, limit)
        ).fetchall()
        
    reminders = [dict(r) for r in rows]
    con.close()
    return reminders

def complete_reminder(reminder_id: int) -> dict | bool | None:
    """Complete a local reminder, advancing its next occurrence if it is recurring.

    For one-shot reminders, sets status to 'completed' and returns True.
    For recurring reminders, recalculates the next due date, resets status to
    'pending', and returns a dict with {'recurred': True, 'next_due': str}.
    Returns None if the reminder was not found.

    Args:
        reminder_id: Database row ID.

    Returns:
        dict | bool | None: Completion outcome — see above.
    """
    con = sqlite3.connect(cfg.CHAT_DB_PATH)
    con.row_factory = sqlite3.Row

    row = con.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    if not row:
        con.close()
        return None

    recurrence_rule = row["recurrence_rule"] if "recurrence_rule" in row.keys() else None
    next_due = _next_due(recurrence_rule, row["due_at"]) if recurrence_rule else None

    if next_due:
        # Recurring: reset to pending with the next due date
        con.execute(
            "UPDATE reminders SET due_at = ?, status = 'pending', notified = 0 WHERE id = ?",
            (next_due, reminder_id),
        )
        con.commit()
        con.close()
        return {"recurred": True, "next_due": next_due}
    else:
        # One-shot: mark completed
        cur = con.execute(
            "UPDATE reminders SET status = 'completed' WHERE id = ?", (reminder_id,)
        )
        success = cur.rowcount > 0
        con.commit()
        con.close()
        return success if success else None

def delete_reminder(reminder_id: int) -> bool:
    """Delete a local reminder.

    Args:
        reminder_id: Database row ID.

    Returns:
        bool: True if deleted, False otherwise.
    """
    con = sqlite3.connect(cfg.CHAT_DB_PATH)
    cur = con.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    success = cur.rowcount > 0
    con.commit()
    con.close()
    return success

def get_unified_agenda(days: int = 7) -> list:
    """Generate a combined, chronological agenda of local reminders and cached GCal events.

    Args:
        days: Number of days forward to include.

    Returns:
        list: Unified agenda items, sorted chronologically by start/due time.
    """
    agenda = []
    
    # 1. Fetch local reminders (pending ones, and any that fell due in the last 24h)
    con = sqlite3.connect(cfg.CHAT_DB_PATH)
    con.row_factory = sqlite3.Row
    
    now = datetime.datetime.utcnow()
    time_max = (now + datetime.timedelta(days=days)).isoformat().replace("T", " ")
    
    rows = con.execute(
        """
        SELECT id, title, description, due_at, status, created_at
        FROM reminders
        WHERE status = 'pending' AND due_at <= ?
        ORDER BY due_at ASC
        """,
        (time_max,)
    ).fetchall()
    
    for r in rows:
        agenda.append({
            "type": "reminder",
            "id": r["id"],
            "title": r["title"],
            "description": r["description"],
            "time": r["due_at"],
            "status": r["status"]
        })
        
    con.close()
    
    # 2. Fetch cached GCal events
    gcal_events = get_cached_gcal_events(days_back=1, days_forward=days)
    for event in gcal_events:
        # Normalize ISO start time to a readable format
        time_str = event["start_at"].replace("T", " ").split("+")[0].split("Z")[0]
        agenda.append({
            "type": "calendar_event",
            "id": event["id"],
            "title": event["summary"],
            "description": event["description"],
            "time": time_str,
            "location": event.get("location", ""),
            "source": event["source"]
        })
        
    # 3. Sort chronologically
    agenda.sort(key=lambda x: x["time"])
    return agenda
