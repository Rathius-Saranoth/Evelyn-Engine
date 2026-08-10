# gcal_sync.py
# date created: 2026-06-19
# date modified: 2026-06-27
# tags: #gcal, #sync, #google-calendar, #offline-first, #caching

"""gcal_sync.py — Google Calendar Synchronizer and Local Event Cache.

Pulls events from Google Calendar, updates the local SQLite cache, and handles
authentication token refreshes and network errors gracefully to support offline-first.
"""

import os
import sqlite3
import datetime
import time
import evelyn_config as cfg

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

def get_gcal_service():
    """Load cached OAuth credentials, refresh if expired, and build GCal service.

    Returns:
        googleapiclient.discovery.Resource: Google Calendar service resource, or None.
    """
    token_path = cfg.GCAL_TOKEN_PATH
    if not os.path.exists(token_path):
        return None

    try:
        creds = Credentials.from_authorized_user_file(token_path)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save the refreshed token
            with open(token_path, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())
        return build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        # Check token file age to see if it likely hit the 7-day GCloud Testing limit
        file_age_days = 0.0
        try:
            mtime = os.path.getmtime(token_path)
            ctime = os.path.getctime(token_path)
            file_age_days = (time.time() - min(ctime, mtime)) / 86400.0
        except Exception:
            try:
                file_age_days = (time.time() - os.path.getmtime(token_path)) / 86400.0
            except Exception:
                pass

        if file_age_days > 7.0:
            print(f"[GCal Sync] Error loading credentials: {e}\n"
                  f"[GCal Sync] Notice: Token file is {file_age_days:.1f} days old. "
                  f"Since your Google Cloud App is in 'Testing' mode, tokens expire every 7 days.\n"
                  f"[GCal Sync] Please run 'python scripts/setup_gcal.py' to re-authenticate.", flush=True)
        else:
            print(f"[GCal Sync] Error loading credentials: {e}", flush=True)
        return None

def sync_gcal_events(days_back: int = 7, days_forward: int = 30) -> dict:
    """Pull events from Google Calendar and cache them in the SQLite database.

    Gracefully catches network or configuration errors for offline-first resilience.

    Args:
        days_back: Number of past days to sync.
        days_forward: Number of future days to sync.

    Returns:
        dict: Sync outcome summary.
    """
    service = get_gcal_service()
    if not service:
        return {
            "status": "unconfigured",
            "message": "Google Calendar token not found or expired. Run scripts/setup_gcal.py."
        }

    try:
        now = datetime.datetime.utcnow()
        time_min = (now - datetime.timedelta(days=days_back)).isoformat() + "Z"
        time_max = (now + datetime.timedelta(days=days_forward)).isoformat() + "Z"

        print(f"[GCal Sync] Pulling events between {time_min} and {time_max}...", flush=True)
        events_result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])
        print(f"[GCal Sync] Fetched {len(events)} events from Google Calendar.", flush=True)

        con = sqlite3.connect(cfg.CHAT_DB_PATH)
        con.row_factory = sqlite3.Row
        
        # Clear existing GCal cache entries in this range so we reflect cancellations/updates
        # This keeps the cache accurate without needing full deletion of past events.
        con.execute(
            "DELETE FROM calendar_events WHERE source = 'google' AND start_at >= ? AND end_at <= ?",
            (time_min, time_max)
        )

        sync_time = datetime.datetime.utcnow().isoformat()
        inserted_count = 0

        for event in events:
            event_id = event.get("id")
            summary = event.get("summary", "No Title")
            description = event.get("description", "")
            location = event.get("location", "")
            
            # Start and End times (handles all-day events vs standard events)
            start_info = event.get("start", {})
            end_info = event.get("end", {})
            
            start_at = start_info.get("dateTime") or start_info.get("date")
            end_at = end_info.get("dateTime") or end_info.get("date")
            
            if not start_at or not end_at:
                continue

            con.execute(
                """
                INSERT OR REPLACE INTO calendar_events (id, summary, description, start_at, end_at, location, source, last_sync)
                VALUES (?, ?, ?, ?, ?, ?, 'google', ?)
                """,
                (event_id, summary, description, start_at, end_at, location, sync_time)
            )
            inserted_count += 1

        con.commit()
        con.close()
        return {
            "status": "success",
            "message": f"Successfully synced and cached {inserted_count} events.",
            "count": inserted_count
        }

    except Exception as e:
        print(f"[GCal Sync] Failed to sync: {e}. Using cached events.", flush=True)
        return {
            "status": "offline",
            "message": f"Network or API error: {e}. Using cached events."
        }

def get_cached_gcal_events(days_back: int = 7, days_forward: int = 30) -> list:
    """Retrieve Google Calendar events from the local SQLite cache.

    Args:
        days_back: Number of past days to query.
        days_forward: Number of future days to query.

    Returns:
        list: List of dictionary representations of events.
    """
    try:
        con = sqlite3.connect(cfg.CHAT_DB_PATH)
        con.row_factory = sqlite3.Row
        
        now = datetime.datetime.utcnow()
        time_min = (now - datetime.timedelta(days=days_back)).isoformat()
        time_max = (now + datetime.timedelta(days=days_forward)).isoformat()
        
        rows = con.execute(
            """
            SELECT id, summary, description, start_at, end_at, location, source
            FROM calendar_events
            WHERE source = 'google' AND start_at >= ? AND start_at <= ?
            ORDER BY start_at ASC
            """,
            (time_min, time_max)
        ).fetchall()
        
        events = [dict(r) for r in rows]
        con.close()
        return events
    except Exception as e:
        print(f"[GCal Sync] Error reading cache: {e}", flush=True)
        return []


def parse_local_datetime(dt_str: str) -> datetime.datetime:
    """Parse a datetime string in local time, adding the local timezone information.

    Args:
        dt_str: String in format 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD' or ISO-8601 format.

    Returns:
        datetime.datetime: Timezone-aware datetime object.
    """
    dt_str = dt_str.strip().replace("T", " ")
    
    # Try parsing date-only first (normalize to midnight)
    if len(dt_str) <= 10:
        dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d")
    else:
        # Try 'YYYY-MM-DD HH:MM:SS'
        try:
            dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            # Fall back to standard ISO parsing if offset/etc is already present
            dt = datetime.datetime.fromisoformat(dt_str)
            
    if dt.tzinfo is None:
        # Make it aware using local timezone
        dt = dt.astimezone()
    return dt


def create_gcal_event(
    summary: str,
    start_at: str,
    end_at: str = None,
    description: str = None,
    location: str = None,
    recurrence: list = None
) -> dict:
    """Create a new event on Google Calendar, then cache it locally.

    Args:
        summary: Title of the event.
        start_at: Start time of the event (local time 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD' or ISO-8601).
        end_at: End time of the event. If None, defaults to 1 hour after start_at (or next day if all-day).
        description: Optional notes/description.
        location: Optional location.
        recurrence: Optional list of recurrence rules (e.g. ['RRULE:FREQ=DAILY']).

    Returns:
        dict: Sync outcome summary with 'status' and 'message' (and 'event_id' on success).
    """
    service = get_gcal_service()
    if not service:
        return {
            "status": "unconfigured",
            "message": "Google Calendar token not found or expired. Run scripts/setup_gcal.py."
        }

    try:
        # Parse start time
        start_dt = parse_local_datetime(start_at)
        is_all_day = (len(start_at.strip()) <= 10)

        # Calculate or parse end time
        if end_at:
            end_dt = parse_local_datetime(end_at)
        else:
            if is_all_day:
                end_dt = start_dt + datetime.timedelta(days=1)
            else:
                end_dt = start_dt + datetime.timedelta(hours=1)

        # Build request body
        event_body = {
            "summary": summary,
            "description": description or "",
            "location": location or ""
        }

        if is_all_day:
            event_body["start"] = {"date": start_dt.strftime("%Y-%m-%d")}
            event_body["end"] = {"date": end_dt.strftime("%Y-%m-%d")}
        else:
            event_body["start"] = {"dateTime": start_dt.isoformat()}
            event_body["end"] = {"dateTime": end_dt.isoformat()}

        if recurrence:
            event_body["recurrence"] = recurrence

        print(f"[GCal Sync] Creating event: {summary} at {start_at}...", flush=True)
        created_event = service.events().insert(
            calendarId="primary",
            body=event_body
        ).execute()

        event_id = created_event.get("id")
        print(f"[GCal Sync] Event created successfully on Google Calendar. ID: {event_id}", flush=True)

        # Immediately update the local cache so the event is visible without a full sync
        con = sqlite3.connect(cfg.CHAT_DB_PATH)
        sync_time = datetime.datetime.utcnow().isoformat()
        
        # Save event to local sqlite cache
        con.execute(
            """
            INSERT OR REPLACE INTO calendar_events (id, summary, description, start_at, end_at, location, source, last_sync)
            VALUES (?, ?, ?, ?, ?, ?, 'google', ?)
            """,
            (
                event_id,
                created_event.get("summary", "No Title"),
                created_event.get("description", ""),
                start_dt.isoformat() if not is_all_day else start_dt.strftime("%Y-%m-%d"),
                end_dt.isoformat() if not is_all_day else end_dt.strftime("%Y-%m-%d"),
                created_event.get("location", ""),
                sync_time
            )
        )
        con.commit()
        con.close()

        return {
            "status": "success",
            "message": f"Successfully created calendar event: '{summary}' on Google Calendar.",
            "event_id": event_id
        }

    except Exception as e:
        print(f"[GCal Sync] Failed to create event: {e}", flush=True)
        return {
            "status": "error",
            "message": f"API error: {e}"
        }


def delete_gcal_event(event_id_or_query: str, target_date: str = None) -> dict:
    """Delete an event from Google Calendar and the local cache.

    Accepts either an exact event ID or an event title/summary query.
    If target_date is provided ('YYYY-MM-DD'), limits title matches to that specific date.
    If multiple events match and target_date is ambiguous, returns a list of matching candidates.

    Args:
        event_id_or_query: The ID or title/summary of the calendar event to delete.
        target_date: Optional target date string ('YYYY-MM-DD') to ensure the correct event is selected.

    Returns:
        dict: Sync outcome summary with 'status' and 'message'.
    """
    service = get_gcal_service()
    if not service:
        return {
            "status": "unconfigured",
            "message": "Google Calendar token not found or expired. Run scripts/setup_gcal.py."
        }

    target_id = event_id_or_query.strip()
    clean_date = target_date.strip()[:10] if target_date else None
    resolved_id = None
    candidates = []

    # 1. Direct raw ID check in local cache
    try:
        con = sqlite3.connect(cfg.CHAT_DB_PATH)
        row = con.execute("SELECT id, summary, start_at FROM calendar_events WHERE id = ?", (target_id,)).fetchone()
        if row:
            resolved_id = row[0]
        else:
            # Search by title in local cache
            rows = con.execute(
                "SELECT id, summary, start_at FROM calendar_events WHERE summary LIKE ? ORDER BY start_at DESC",
                (f"%{target_id}%",)
            ).fetchall()
            if rows:
                if clean_date:
                    date_matched = [r for r in rows if r[2] and r[2].startswith(clean_date)]
                    candidates = date_matched if date_matched else rows
                else:
                    candidates = rows
        con.close()
    except Exception as e:
        print(f"[GCal Sync] Error checking local cache: {e}", flush=True)

    # 2. If no direct ID and no candidates in cache, search GCal API directly
    if not resolved_id and not candidates:
        try:
            events_result = service.events().list(calendarId="primary", q=target_id, maxResults=10).execute()
            items = events_result.get("items", [])
            for item in items:
                e_id = item.get("id")
                e_summary = item.get("summary", "")
                e_start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date", "")
                candidates.append((e_id, e_summary, e_start))

            if clean_date and candidates:
                date_matched = [c for c in candidates if c[2] and c[2].startswith(clean_date)]
                if date_matched:
                    candidates = date_matched
        except Exception as e:
            print(f"[GCal Sync] Error searching GCal API: {e}", flush=True)

    # Resolve candidates
    if not resolved_id:
        if len(candidates) == 1:
            resolved_id = candidates[0][0]
        elif len(candidates) > 1:
            # Disambiguation needed!
            matches_str = "; ".join([f"'{c[1]}' on {c[2][:10]} (ID: {c[0]})" for c in candidates[:5]])
            return {
                "status": "ambiguous",
                "message": (
                    f"Found {len(candidates)} events matching '{target_id}'. "
                    f"Please specify the target_date or event_id to delete. Matching options: {matches_str}"
                )
            }
        else:
            resolved_id = target_id  # fallback to raw target_id string

    try:
        print(f"[GCal Sync] Deleting event '{target_id}' (ID: {resolved_id}) from Google Calendar...", flush=True)
        service.events().delete(
            calendarId="primary",
            eventId=resolved_id
        ).execute()

        # Immediately update the local cache
        con = sqlite3.connect(cfg.CHAT_DB_PATH)
        con.execute("DELETE FROM calendar_events WHERE id = ? OR id = ?", (resolved_id, target_id))
        con.commit()
        con.close()

        return {
            "status": "success",
            "message": f"Successfully deleted calendar event '{target_id}' (ID: {resolved_id})."
        }
    except Exception as e:
        print(f"[GCal Sync] Failed to delete event {resolved_id}: {e}", flush=True)
        return {
            "status": "error",
            "message": f"API error deleting '{target_id}': {e}"
        }

