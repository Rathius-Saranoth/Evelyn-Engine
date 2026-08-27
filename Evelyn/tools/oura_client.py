# oura_client.py
# date created: 2026-08-16
# tags: #oura, #oura-ring, #sleep, #readiness, #vitals, #api

"""oura_client.py — Oura Ring Cloud API v2 Client for Evelyn Engine.

Fetches live, real-time sleep scores, granular sleep stages, readiness and recovery
metrics, daily activity, and daytime stress directly from Oura Cloud.
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import evelyn_config as cfg

OURA_BASE_URL = "https://api.ouraring.com/v2/usercollection"


def _get_access_token() -> Optional[str]:
    """Retrieve the Oura Personal Access Token from config or environment."""
    token = os.environ.get("OURA_ACCESS_TOKEN")
    if token:
        return token.strip()

    token_path = getattr(cfg, "OURA_TOKEN_PATH", r"/home/rathius/evelyn/data/oura_token.json")
    if os.path.exists(token_path):
        try:
            with open(token_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("access_token")
        except Exception as e:
            print(f"[Oura Client] Error loading token file: {e}", flush=True)
    return None


def _get_headers() -> Optional[dict]:
    """Build Authorization headers for Oura API requests."""
    token = _get_access_token()
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _fetch_endpoint(endpoint: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> list:
    """Fetch items from an Oura usercollection endpoint within a date range."""
    headers = _get_headers()
    if not headers:
        return []

    if not end_date:
        end_date = date.today().strftime("%Y-%m-%d")
    if not start_date:
        start_date = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")

    url = f"{OURA_BASE_URL}/{endpoint}?start_date={start_date}&end_date={end_date}"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("data", [])
        print(f"[Oura Client] API {endpoint} returned status {resp.status_code}: {resp.text}", flush=True)
        return []
    except Exception as e:
        print(f"[Oura Client] Request error on {endpoint}: {e}", flush=True)
        return []


def get_daily_sleep(start_date: Optional[str] = None, end_date: Optional[str] = None) -> list:
    """Fetch daily sleep scores and contributor metrics."""
    return _fetch_endpoint("daily_sleep", start_date, end_date)


def get_sleep_sessions(start_date: Optional[str] = None, end_date: Optional[str] = None) -> list:
    """Fetch detailed sleep session objects (hypnogram stages, HR, HRV, efficiency)."""
    return _fetch_endpoint("sleep", start_date, end_date)


def get_daily_readiness(start_date: Optional[str] = None, end_date: Optional[str] = None) -> list:
    """Fetch daily readiness scores and recovery contributors."""
    return _fetch_endpoint("daily_readiness", start_date, end_date)


def get_daily_activity(start_date: Optional[str] = None, end_date: Optional[str] = None) -> list:
    """Fetch daily activity records (steps, active calories, target scores)."""
    return _fetch_endpoint("daily_activity", start_date, end_date)


def get_daily_stress(start_date: Optional[str] = None, end_date: Optional[str] = None) -> list:
    """Fetch daily stress and daytime recovery metrics."""
    return _fetch_endpoint("daily_stress", start_date, end_date)


def get_workouts(start_date: Optional[str] = None, end_date: Optional[str] = None) -> list:
    """Fetch recorded workout and activity sessions from Oura."""
    return _fetch_endpoint("workout", start_date, end_date)


def get_sessions(start_date: Optional[str] = None, end_date: Optional[str] = None) -> list:
    """Fetch recorded restorative/meditation/breathwork sessions from Oura."""
    return _fetch_endpoint("session", start_date, end_date)


def get_heart_rate_series(
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    hours: Optional[float] = None,
) -> list:
    """Fetch high-resolution live heart rate readings from Oura API.

    Args:
        start_datetime: ISO datetime string (e.g. '2026-08-27T09:00:00Z').
        end_datetime: ISO datetime string (e.g. '2026-08-27T11:00:00Z').
        hours: Optional convenience float to fetch the last N hours of readings.

    Returns:
        list: List of dicts with 'timestamp', 'bpm', and 'source' ('workout', 'awake', 'rest', 'sleep').
    """
    headers = _get_headers()
    if not headers:
        return []

    now_utc = datetime.now(timezone.utc)
    if hours is not None and hours > 0:
        start_dt = now_utc - timedelta(hours=hours)
        start_datetime = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_datetime = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        if not end_datetime:
            end_datetime = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        if not start_datetime:
            start_datetime = (now_utc - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
    }
    url = f"{OURA_BASE_URL}/heartrate"
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("data", [])
        print(f"[Oura Client] API heartrate returned status {resp.status_code}: {resp.text}", flush=True)
        return []
    except Exception as e:
        print(f"[Oura Client] Request error on heartrate: {e}", flush=True)
        return []


def get_today_overview() -> dict:
    """Retrieve a comprehensive live health summary for today from Oura Ring.

    Returns:
        Structured dictionary containing sleep score & breakdown, readiness,
        activity, and daytime recovery metrics.
    """
    today_str = date.today().strftime("%Y-%m-%d")
    yesterday_str = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    start_str = (date.today() - timedelta(days=2)).strftime("%Y-%m-%d")

    # Fetch live data
    sleep_scores = get_daily_sleep(start_str, today_str)
    sleep_sessions = get_sleep_sessions(start_str, today_str)
    readiness_list = get_daily_readiness(start_str, today_str)
    activity_list = get_daily_activity(start_str, today_str)
    stress_list = get_daily_stress(start_str, today_str)

    latest_sleep_score = sleep_scores[-1] if sleep_scores else None
    latest_sleep_session = sleep_sessions[-1] if sleep_sessions else None
    latest_readiness = readiness_list[-1] if readiness_list else None
    latest_activity = activity_list[-1] if activity_list else None
    latest_stress = stress_list[-1] if stress_list else None

    # Parse Sleep Details
    sleep_data = None
    if latest_sleep_session:
        total_sec = latest_sleep_session.get("total_sleep_duration", 0)
        deep_sec = latest_sleep_session.get("deep_sleep_duration", 0)
        rem_sec = latest_sleep_session.get("rem_sleep_duration", 0)
        light_sec = latest_sleep_session.get("light_sleep_duration", 0)
        awake_sec = latest_sleep_session.get("awake_time", 0)

        total_hours = round(total_sec / 3600.0, 2)
        sleep_data = {
            "date": latest_sleep_session.get("day"),
            "score": latest_sleep_score.get("score") if latest_sleep_score else None,
            "bedtime_start": latest_sleep_session.get("bedtime_start"),
            "bedtime_end": latest_sleep_session.get("bedtime_end"),
            "total_sleep_hours": total_hours,
            "efficiency_percent": latest_sleep_session.get("efficiency"),
            "resting_heart_rate_bpm": latest_sleep_session.get("lowest_heart_rate") or latest_sleep_session.get("average_heart_rate"),
            "average_hrv_rmssd_ms": latest_sleep_session.get("average_hrv"),
            "average_breath_rate": latest_sleep_session.get("average_breath"),
            "stages_minutes": {
                "Deep Sleep": round(deep_sec / 60.0, 1),
                "REM Sleep": round(rem_sec / 60.0, 1),
                "Light Sleep": round(light_sec / 60.0, 1),
                "Awake": round(awake_sec / 60.0, 1),
            },
            "contributors": latest_sleep_score.get("contributors") if latest_sleep_score else {},
        }

    # Parse Readiness Details
    readiness_data = None
    if latest_readiness:
        readiness_data = {
            "date": latest_readiness.get("day"),
            "score": latest_readiness.get("score"),
            "temperature_deviation_c": latest_readiness.get("temperature_deviation"),
            "contributors": latest_readiness.get("contributors", {}),
        }

    # Parse Activity Details
    activity_data = None
    if latest_activity:
        activity_data = {
            "date": latest_activity.get("day"),
            "score": latest_activity.get("score"),
            "steps": latest_activity.get("steps"),
            "active_calories_kcal": latest_activity.get("active_calories"),
            "total_calories_kcal": latest_activity.get("total_calories"),
            "target_calories_kcal": latest_activity.get("target_calories"),
            "equivalent_walking_distance_meters": latest_activity.get("equivalent_walking_distance"),
        }

    # Parse Stress Details
    stress_data = None
    if latest_stress:
        stress_data = {
            "date": latest_stress.get("day"),
            "day_summary": latest_stress.get("day_summary"),
            "stress_duration_minutes": round(latest_stress.get("stress_high", 0) / 60.0, 1),
            "recovery_duration_minutes": round(latest_stress.get("recovery_high", 0) / 60.0, 1),
        }

    return {
        "status": "success",
        "source": "Oura Ring API (Live)",
        "query_date": today_str,
        "sleep": sleep_data,
        "readiness": readiness_data,
        "activity": activity_data,
        "stress": stress_data,
    }
