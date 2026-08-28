# gtasks_sync.py
# date created: 2026-08-23
# tags: #gtasks, #google-tasks, #tasks, #sync, #offline-first, #caching

"""gtasks_sync.py — Google Tasks Synchronizer and Local Task Cache.

Pulls tasks from Google Tasks, updates the local SQLite cache in evelyn_chat.db,
and handles authentication token refreshes and network errors gracefully to support offline-first operations.
"""

from __future__ import annotations

import contextlib
import datetime
import os
import shutil
import sqlite3
import time
from typing import Any

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import evelyn_config as cfg


def get_gtasks_credentials(token_path: str | None = None) -> Credentials | None:
    """Retrieve and refresh valid Google Tasks OAuth credentials.

    Falls back to checking GDRIVE_TOKEN_PATH if GTASKS_TOKEN_PATH does not exist yet.

    Args:
        token_path: Path to token JSON. Defaults to cfg.GTASKS_TOKEN_PATH.

    Returns:
        Credentials object if valid or refreshed, None otherwise.
    """
    path = token_path or cfg.GTASKS_TOKEN_PATH

    # Fallback to GDRIVE_TOKEN_PATH if gtasks token doesn't exist
    if not os.path.exists(path) and hasattr(cfg, "GDRIVE_TOKEN_PATH") and os.path.exists(cfg.GDRIVE_TOKEN_PATH):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copy(cfg.GDRIVE_TOKEN_PATH, path)
            print(f"[GTasks Sync] Copied credentials from {cfg.GDRIVE_TOKEN_PATH} -> {path}", flush=True)
        except OSError as e:
            print(f"[GTasks Sync] Warning: could not copy GDRIVE token: {e}", flush=True)

    if not os.path.exists(path):
        return None

    try:
        creds = Credentials.from_authorized_user_file(path)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed credentials
            with open(path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        return creds if creds and creds.valid else None
    except (GoogleAuthError, OSError, ValueError, KeyError) as e:
        file_age_days = 0.0
        with contextlib.suppress(OSError):
            mtime = os.path.getmtime(path)
            file_age_days = (time.time() - mtime) / 86400.0

        if file_age_days > 7.0:
            print(
                f"[GTasks Sync] Error loading credentials: {e}\n"
                f"[GTasks Sync] Notice: Token file is {file_age_days:.1f} days old. "
                f"Since your Google Cloud App is in 'Testing' mode, tokens expire every 7 days.\n"
                f"[GTasks Sync] Please run 'python scripts/setup_gtasks.py' to re-authenticate.",
                flush=True,
            )
        else:
            print(f"[GTasks Sync] Error loading credentials: {e}", flush=True)
        return None


def get_gtasks_service() -> Any:
    """Build and return an authorized Google Tasks API service resource.

    Returns:
        googleapiclient.discovery.Resource: Google Tasks service resource, or None.
    """
    creds = get_gtasks_credentials()
    if not creds:
        return None
    return build("tasks", "v1", credentials=creds, cache_discovery=False)


def parse_due_datetime(due_str: str | None) -> str | None:
    """Normalize a due date/time string into RFC 3339 timestamp format.

    Args:
        due_str: Date/time string in formats like 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS', or ISO-8601.

    Returns:
        RFC 3339 formatted string (e.g. '2026-08-24T12:00:00.000Z') or None if invalid/empty.
    """
    if not due_str:
        return None
    clean = due_str.strip()
    if not clean:
        return None

    # Handle ISO-8601 strings ending in Z or offset
    if "T" in clean:
        try:
            dt = datetime.datetime.fromisoformat(clean.replace("Z", "+00:00"))
            return dt.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except ValueError:
            pass

    # Handle 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD HH:MM'
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.datetime.strptime(clean, fmt).replace(tzinfo=datetime.UTC)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except ValueError:
            continue

    # Handle date-only 'YYYY-MM-DD' or 'YYYY/MM/DD'
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.datetime.strptime(clean, fmt).replace(tzinfo=datetime.UTC)
            return dt.strftime("%Y-%m-%dT00:00:00.000Z")
        except ValueError:
            continue

    return None


def sync_gtasks(tasklist: str = "@default") -> dict[str, Any]:
    """Pull tasks from Google Tasks and cache them in the SQLite database.

    Gracefully catches network or configuration errors for offline-first resilience.

    Args:
        tasklist: The Google Tasks task list identifier. Defaults to '@default'.

    Returns:
        dict: Sync outcome summary.
    """
    service = get_gtasks_service()
    if not service:
        return {
            "status": "unconfigured",
            "message": "Google Tasks token not found or expired. Run scripts/setup_gtasks.py.",
        }

    try:
        print(f"[GTasks Sync] Fetching tasks from tasklist '{tasklist}'...", flush=True)
        response = service.tasks().list(
            tasklist=tasklist,
            showCompleted=True,
            showHidden=True,
            maxResults=100,
        ).execute()

        items = response.get("items", [])
        print(f"[GTasks Sync] Fetched {len(items)} tasks from Google Tasks.", flush=True)

        con = sqlite3.connect(cfg.CHAT_DB_PATH)
        con.row_factory = sqlite3.Row
        try:
            now_iso = datetime.datetime.now(datetime.UTC).isoformat()
            fetched_ids = set()

            for item in items:
                task_id = item.get("id")
                if not task_id:
                    continue
                fetched_ids.add(task_id)

                title = item.get("title", "Untitled Task")
                notes = item.get("notes") or ""
                due_at = item.get("due")  # RFC 3339 string
                status = item.get("status", "needsAction")
                completed_at = item.get("completed")

                con.execute(
                    """
                    INSERT OR REPLACE INTO tasks
                    (id, tasklist_id, title, notes, due_at, status, completed_at, source, last_sync)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'google', ?)
                    """,
                    (task_id, tasklist, title, notes, due_at, status, completed_at, now_iso),
                )

            con.commit()
        finally:
            con.close()

        return {
            "status": "success",
            "message": f"Successfully synced {len(items)} task(s) from Google Tasks.",
            "count": len(items),
        }

    except (HttpError, GoogleAuthError, sqlite3.Error, OSError, ValueError) as e:
        print(f"[GTasks Sync] Error during sync: {e}", flush=True)
        return {
            "status": "offline",
            "message": f"Network or API error while syncing tasks: {e}. Utilizing local SQLite cache.",
        }


def get_cached_tasks(
    include_completed: bool = False,
    due_within_days: int | None = None,
    tasklist: str = "@default",
) -> list[dict[str, Any]]:
    """Retrieve tasks from the local SQLite cache.

    Args:
        include_completed: If True, includes completed tasks. Defaults to False.
        due_within_days: If specified, filters tasks due within the next N days.
        tasklist: The task list identifier. Defaults to '@default'.

    Returns:
        list[dict]: List of task dictionaries.
    """
    if not os.path.exists(cfg.CHAT_DB_PATH):
        return []

    try:
        con = sqlite3.connect(cfg.CHAT_DB_PATH)
        con.row_factory = sqlite3.Row
        try:
            query = "SELECT * FROM tasks WHERE tasklist_id = ?"
            params: list[Any] = [tasklist]

            if not include_completed:
                query += " AND status = 'needsAction'"

            if due_within_days is not None:
                now_utc = datetime.datetime.now(datetime.UTC)
                max_due = (now_utc + datetime.timedelta(days=due_within_days)).strftime("%Y-%m-%dT23:59:59.999Z")
                query += " AND (due_at IS NULL OR due_at <= ?)"
                params.append(max_due)

            query += " ORDER BY (due_at IS NULL), due_at ASC, id ASC"

            cursor = con.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            con.close()
    except (sqlite3.Error, OSError, ValueError) as e:
        print(f"[GTasks Sync] Error querying cached tasks: {e}", flush=True)
        return []


def create_gtask(
    title: str,
    due: str | None = None,
    notes: str | None = None,
    tasklist: str = "@default",
) -> dict[str, Any]:
    """Create a new task on Google Tasks and cache it locally.

    Args:
        title: Title of the task.
        due: Optional due date/time (e.g. 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS', or RFC 3339).
        notes: Optional notes or description.
        tasklist: Tasklist ID. Defaults to '@default'.

    Returns:
        dict: Outcome summary with 'status', 'message', and 'task_id' on success.
    """
    service = get_gtasks_service()
    if not service:
        return {
            "status": "unconfigured",
            "message": "Google Tasks token not found or expired. Run scripts/setup_gtasks.py.",
        }

    try:
        body: dict[str, Any] = {
            "title": title,
            "notes": notes or "",
        }

        due_rfc = parse_due_datetime(due) if due else None
        if due_rfc:
            body["due"] = due_rfc

        print(f"[GTasks Sync] Creating task: '{title}' (due: {due_rfc})...", flush=True)
        created_task = service.tasks().insert(
            tasklist=tasklist,
            body=body,
        ).execute()

        task_id = created_task.get("id")
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()

        # Cache locally
        con = sqlite3.connect(cfg.CHAT_DB_PATH)
        try:
            con.execute(
                """
                INSERT OR REPLACE INTO tasks
                (id, tasklist_id, title, notes, due_at, status, completed_at, source, last_sync)
                VALUES (?, ?, ?, ?, ?, 'needsAction', NULL, 'google', ?)
                """,
                (task_id, tasklist, title, notes or "", due_rfc, now_iso),
            )
            con.commit()
        finally:
            con.close()

        return {
            "status": "success",
            "message": f"Successfully created task '{title}' on Google Tasks.",
            "task_id": task_id,
            "task": created_task,
        }

    except (HttpError, GoogleAuthError, sqlite3.Error, OSError, ValueError) as e:
        print(f"[GTasks Sync] Error creating task: {e}", flush=True)
        return {
            "status": "error",
            "message": f"Failed to create task on Google Tasks: {e}",
        }


def complete_gtask(task_id: str, tasklist: str = "@default") -> dict[str, Any]:
    """Mark a task as completed on Google Tasks and update the local SQLite cache.

    Args:
        task_id: The unique ID of the Google Task.
        tasklist: Tasklist ID. Defaults to '@default'.

    Returns:
        dict: Outcome summary with 'status' and 'message'.
    """
    service = get_gtasks_service()
    if not service:
        return {
            "status": "unconfigured",
            "message": "Google Tasks token not found or expired. Run scripts/setup_gtasks.py.",
        }

    try:
        print(f"[GTasks Sync] Marking task {task_id} as completed...", flush=True)
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()

        service.tasks().patch(
            tasklist=tasklist,
            task=task_id,
            body={"status": "completed"},
        ).execute()

        con = sqlite3.connect(cfg.CHAT_DB_PATH)
        try:
            con.execute(
                """
                UPDATE tasks
                SET status = 'completed', completed_at = ?, last_sync = ?
                WHERE id = ?
                """,
                (now_iso, now_iso, task_id),
            )
            con.commit()
        finally:
            con.close()

        return {
            "status": "success",
            "message": f"Successfully marked task {task_id} as completed.",
            "task_id": task_id,
        }

    except (HttpError, GoogleAuthError, sqlite3.Error, OSError, ValueError) as e:
        print(f"[GTasks Sync] Error completing task {task_id}: {e}", flush=True)
        return {
            "status": "error",
            "message": f"Failed to complete task on Google Tasks: {e}",
        }


def delete_gtask(task_id: str, tasklist: str = "@default") -> dict[str, Any]:
    """Delete a task from Google Tasks and remove it from the local SQLite cache.

    Args:
        task_id: The unique ID of the Google Task.
        tasklist: Tasklist ID. Defaults to '@default'.

    Returns:
        dict: Outcome summary with 'status' and 'message'.
    """
    service = get_gtasks_service()
    if not service:
        return {
            "status": "unconfigured",
            "message": "Google Tasks token not found or expired. Run scripts/setup_gtasks.py.",
        }

    try:
        print(f"[GTasks Sync] Deleting task {task_id}...", flush=True)
        service.tasks().delete(
            tasklist=tasklist,
            task=task_id,
        ).execute()

        con = sqlite3.connect(cfg.CHAT_DB_PATH)
        try:
            con.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            con.commit()
        finally:
            con.close()

        return {
            "status": "success",
            "message": f"Successfully deleted task {task_id}.",
            "task_id": task_id,
        }

    except (HttpError, GoogleAuthError, sqlite3.Error, OSError, ValueError) as e:
        print(f"[GTasks Sync] Error deleting task {task_id}: {e}", flush=True)
        return {
            "status": "error",
            "message": f"Failed to delete task from Google Tasks: {e}",
        }
