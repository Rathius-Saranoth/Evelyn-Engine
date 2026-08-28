---
title: google_access.md
date created: 2026-08-16 12:55:00
date modified: 2026-08-23 08:03:00
tags: [google, oauth, scopes, permissions, gdrive, gcal, tasks, docs, sheets, reference, evelyn]
---

# Google API Access & OAuth Scopes Reference

> Navigation: [[README.md]] · [[engine_architecture.md]] · [[endpoints.md]]

This document tracks all authorized Google Cloud OAuth 2.0 API scopes and credentials configured for the **Evelyn Engine**. 

> [!IMPORTANT]
> **Maintenance Directive**: Whenever new Google Cloud APIs are enabled or OAuth scopes are added, modified, or revoked in `scripts/setup_gcal.py` or `scripts/setup_gdrive.py`, you **MUST** update this document immediately to maintain synchronization.

---

## 1. Non-Sensitive Scopes

Non-sensitive scopes allow basic availability and read operations for public calendar data.

| Service | API Scope URI | Scope Description | Evelyn Implementation |
| :--- | :--- | :--- | :--- |
| **Google Calendar** | `https://www.googleapis.com/auth/calendar.freebusy` | View availability in your calendars | `scripts/setup_gcal.py`, `Evelyn/tools/gcal_sync.py` |
| **Google Calendar** | `https://www.googleapis.com/auth/calendar.events.freebusy` | See availability on Google calendars you have access to | `scripts/setup_gcal.py`, `Evelyn/tools/gcal_sync.py` |
| **Google Calendar** | `https://www.googleapis.com/auth/calendar.events.public.readonly` | See events on public calendars | `scripts/setup_gcal.py`, `Evelyn/tools/gcal_sync.py` |

---

## 2. Sensitive Scopes

Sensitive scopes request access to private personal user data across Google Calendar, Docs, Sheets, Tasks, and Drive.

| Service | API Scope URI | Scope Description | Evelyn Implementation |
| :--- | :--- | :--- | :--- |
| **Google Calendar** | `https://www.googleapis.com/auth/calendar.readonly` | See and download any calendar you can access | `scripts/setup_gcal.py`, `Evelyn/tools/gcal_sync.py` |
| **Google Calendar** | `https://www.googleapis.com/auth/calendar.events.owned.readonly` | See events on Google calendars you own | `scripts/setup_gcal.py`, `Evelyn/tools/gcal_sync.py` |
| **Google Calendar** | `https://www.googleapis.com/auth/calendar.calendars.readonly` | See calendar metadata, title, and default time zones | `scripts/setup_gcal.py`, `Evelyn/tools/gcal_sync.py` |
| **Google Calendar** | `https://www.googleapis.com/auth/calendar.events` | View and edit events on all your calendars | `scripts/setup_gcal.py`, `Evelyn/tools/evelyn_tools.py` |
| **Google Calendar** | `https://www.googleapis.com/auth/calendar.events.owned` | Create, change, and delete events on your calendars | `scripts/setup_gcal.py`, `Evelyn/tools/evelyn_tools.py` |
| **Google Calendar** | `https://www.googleapis.com/auth/calendar.events.readonly` | View events on all your calendars | `scripts/setup_gcal.py`, `Evelyn/tools/gcal_sync.py` |
| **Google Docs** | `https://www.googleapis.com/auth/documents.readonly` | See all your Google Docs documents | `scripts/setup_gdrive.py`, `Evelyn/tools/gdrive_sync.py`, `scripts/gdrive_knowledge_importer.py` |
| **Google Drive** | `https://www.googleapis.com/auth/drive.apps.readonly` | View your Google Drive connected apps | `scripts/setup_gdrive.py`, `Evelyn/tools/gdrive_sync.py` |
| **Google Sheets** | `https://www.googleapis.com/auth/spreadsheets.readonly` | See all your Google Sheets spreadsheets | `scripts/setup_gdrive.py`, `Evelyn/tools/gdrive_sync.py`, `scripts/gdrive_knowledge_importer.py` |
| **Google Tasks** | `https://www.googleapis.com/auth/tasks` | Create, edit, organize, and delete all your tasks | `scripts/setup_gtasks.py`, `scripts/setup_gdrive.py`, `Evelyn/tools/gtasks_sync.py`, `Evelyn/tools/evelyn_tools.py` |

---

## 3. Restricted Scopes

Restricted scopes request access to sensitive user files and communications.

| Service | API Scope URI | Scope Description | Evelyn Implementation |
| :--- | :--- | :--- | :--- |
| **Google Drive** | `https://www.googleapis.com/auth/drive.readonly` | See and download all your Google Drive files | `scripts/setup_gdrive.py`, `Evelyn/tools/gdrive_sync.py` (Daily `Health Connect.zip` sync), `scripts/gdrive_knowledge_importer.py` |
| **Google Drive** | `https://www.googleapis.com/auth/drive.metadata.readonly` | See information and metadata about your Google Drive files | `scripts/setup_gdrive.py`, `Evelyn/tools/gdrive_sync.py`, `scripts/gdrive_knowledge_importer.py` |
| **Gmail** *(Optional/Future)* | `https://www.googleapis.com/auth/gmail.readonly` | View your email messages and settings | *Planned / Reserved* |

---

## 4. Local Credential & Token Storage

All OAuth credentials and generated refresh tokens are stored locally in the isolated `data/` directory (git-ignored):

- **Client OAuth ID & Secret**: [`data/gcal_credentials.json`](file:///home/rathius/evelyn/data/gcal_credentials.json) / [`data/gdrive_credentials.json`](file:///home/rathius/evelyn/data/gdrive_credentials.json) / [`data/gtasks_credentials.json`](file:///home/rathius/evelyn/data/gtasks_credentials.json)
- **Google Calendar Token**: [`data/gcal_token.json`](file:///home/rathius/evelyn/data/gcal_token.json)
- **Google Tasks Token**: [`data/gtasks_token.json`](file:///home/rathius/evelyn/data/gtasks_token.json)
- **Google Drive & Workspace Token**: [`data/gdrive_token.json`](file:///home/rathius/evelyn/data/gdrive_token.json)

---

## 5. Authorization Setup Commands

To refresh or regenerate OAuth tokens with active scopes:

```bash
# Calendar Setup
/home/rathius/evelyn/venv/bin/python3 scripts/setup_gcal.py

# Google Tasks Setup
/home/rathius/evelyn/venv/bin/python3 scripts/setup_gtasks.py

# Drive, Docs & Sheets Setup
/home/rathius/evelyn/venv/bin/python3 scripts/setup_gdrive.py
```
