# reminders.py
# date created: 2026-06-19
# date modified: 2026-06-19
# tags: #reminders, #agenda, #tasks, #gcal, #offline-first

"""reminders.py — Local Reminders Management & Unified Agenda Aggregator.

Provides local reminder CRUD operations, and compiles local reminders alongside
cached Google Calendar events into a single chronological agenda.
"""

import sqlite3
import datetime
import evelyn_config as cfg
from gcal_sync import get_cached_gcal_events

def create_reminder(title: str, due_at: str, description: str = None) -> dict:
    """Create a new local reminder in the SQLite database.

    Args:
        title: Short title of the reminder.
        due_at: Due timestamp (ISO-8601 string, 'YYYY-MM-DD HH:MM:SS').
        description: Optional detailed notes.

    Returns:
        dict: The created reminder.
    """
    con = sqlite3.connect(cfg.CHAT_DB_PATH)
    con.row_factory = sqlite3.Row
    
    created_at = datetime.datetime.utcnow().isoformat()
    
    # Simple normalization of due_at if user/model passed standard spacing
    due_at = due_at.replace("T", " ")
    
    cur = con.execute(
        """
        INSERT INTO reminders (title, description, due_at, status, created_at, notified)
        VALUES (?, ?, ?, 'pending', ?, 0)
        """,
        (title, description, due_at, created_at)
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

def complete_reminder(reminder_id: int) -> bool:
    """Mark a local reminder as completed.

    Args:
        reminder_id: Database row ID.

    Returns:
        bool: True if updated, False otherwise.
    """
    con = sqlite3.connect(cfg.CHAT_DB_PATH)
    cur = con.execute(
        "UPDATE reminders SET status = 'completed' WHERE id = ?", (reminder_id,)
    )
    success = cur.rowcount > 0
    con.commit()
    con.close()
    return success

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
