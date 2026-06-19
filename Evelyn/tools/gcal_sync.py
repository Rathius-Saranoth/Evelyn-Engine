# gcal_sync.py
# date created: 2026-06-19
# date modified: 2026-06-19
# tags: #gcal, #sync, #google-calendar, #offline-first, #caching

"""gcal_sync.py — Google Calendar Synchronizer and Local Event Cache.

Pulls events from Google Calendar, updates the local SQLite cache, and handles
authentication token refreshes and network errors gracefully to support offline-first.
"""

import os
import sqlite3
import datetime
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
