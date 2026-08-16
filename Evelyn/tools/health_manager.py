# health_manager.py
# date created: 2026-08-16
# tags: #health, #health-connect, #oura, #vitals, #sleep, #fitness, #fhir

"""health_manager.py — Health & Vitals Query Engine for Evelyn.

Aggregates real-time live metrics from Oura Cloud (sleep scores, readiness,
hypnograms, daytime stress) and historical/clinical health records from the
local SQLite Health Connect database.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

# Add Evelyn/tools and project root to path
_tools_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.dirname(os.path.dirname(_tools_dir))
for _p in (_tools_dir, _root_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evelyn_config as cfg

try:
    from . import oura_client
except ImportError:
    import oura_client

# Health Connect Exercise Type mapping
EXERCISE_TYPE_MAP = {
    0: "Other Workout",
    1: "Back Extension",
    2: "Badminton",
    3: "Baseball",
    4: "Basketball",
    5: "Biking",
    8: "Running",
    9: "Running (Treadmill)",
    10: "Calisthenics",
    14: "Cricket",
    15: "Cross Country Skiing",
    16: "Dancing",
    21: "Elliptical",
    25: "Football (American)",
    26: "Football (Australian)",
    27: "Frisbee",
    28: "Gardening",
    29: "Golf",
    30: "Guided Breathing",
    31: "Gymnastics",
    32: "Handball",
    33: "HIIT",
    34: "Hiking",
    35: "Ice Hockey",
    36: "Ice Skating",
    37: "Martial Arts",
    38: "Paddling",
    39: "Paragliding",
    40: "Pilates",
    44: "Rowing",
    45: "Rowing Machine",
    46: "Rugby",
    47: "Sailing",
    48: "Scuba Diving",
    49: "Skateboarding",
    50: "Skating",
    51: "Skiing",
    52: "Snowboarding",
    53: "Walking",
    54: "Squash",
    55: "Stair Climbing",
    56: "Stair Climbing Machine",
    57: "Biking (Stationary)",
    58: "Strength Training",
    59: "Surfing",
    60: "Swimming (Open Water)",
    61: "Swimming (Pool)",
    62: "Table Tennis",
    63: "Tennis",
    64: "Volleyball",
    65: "Water Polo",
    66: "Weightlifting",
    67: "Wheelchair",
    68: "Windsurfing",
    69: "Yoga",
}

# Sleep stage mappings
SLEEP_STAGE_MAP = {
    1: "Awake",
    2: "Sleeping",
    3: "Out of Bed",
    4: "Light Sleep",
    5: "Deep Sleep",
    6: "REM Sleep",
}


def _get_connection() -> Optional[sqlite3.Connection]:
    """Create a read-only SQLite connection to the health database."""
    if not os.path.exists(cfg.HEALTH_DB_PATH):
        return None
    try:
        conn = sqlite3.connect(f"file:{cfg.HEALTH_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"[Health Manager] Connection error: {e}", flush=True)
        return None


def _parse_date_bounds(date_str: Optional[str] = None) -> tuple[int, int, str]:
    """Calculate start and end timestamps (in epoch milliseconds) for a given date."""
    now_local = datetime.now()
    if not date_str or date_str.lower() in ("today", "current"):
        target_d = now_local.date()
    elif date_str.lower() == "yesterday":
        target_d = (now_local - timedelta(days=1)).date()
    else:
        try:
            target_d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_d = now_local.date()

    start_dt = datetime.combine(target_d, datetime.min.time())
    end_dt = datetime.combine(target_d, datetime.max.time())

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    return start_ms, end_ms, target_d.strftime("%Y-%m-%d")


def get_daily_summary(date_str: Optional[str] = None) -> dict:
    """Retrieve an aggregated health and activity summary for a specific day.

    Prioritizes live Oura Ring data for today/yesterday, enriched with SQLite
    workout and biometric records.
    """
    start_ms, end_ms, resolved_date = _parse_date_bounds(date_str)
    today_str = datetime.now().strftime("%Y-%m-%d")

    # If querying today or yesterday, check live Oura data
    if resolved_date == today_str:
        try:
            oura_overview = oura_client.get_today_overview()
            if oura_overview.get("status") == "success" and (oura_overview.get("sleep") or oura_overview.get("readiness")):
                # Check for any logged workouts in local DB
                conn = _get_connection()
                workouts = []
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT exercise_type, title, start_time, end_time FROM exercise_session_record_table WHERE start_time >= ? AND start_time <= ? ORDER BY start_time ASC",
                            (start_ms, end_ms),
                        )
                        for w in cursor.fetchall():
                            dur_mins = round((w["end_time"] - w["start_time"]) / 60000.0, 1)
                            type_name = EXERCISE_TYPE_MAP.get(w["exercise_type"], f"Exercise ({w['exercise_type']})")
                            workouts.append({
                                "type": type_name,
                                "title": w["title"] or type_name,
                                "duration_minutes": dur_mins,
                                "start_time": datetime.fromtimestamp(w["start_time"] / 1000).strftime("%H:%M"),
                            })
                        conn.close()
                    except Exception:
                        if conn:
                            conn.close()

                oura_overview["workouts"] = workouts
                return oura_overview
        except Exception as e:
            print(f"[Health Manager] Oura live query error: {e}", flush=True)

    # Fallback to local SQLite database
    conn = _get_connection()
    if not conn:
        return {"status": "error", "message": "Health database not found and Oura live data unavailable."}

    cursor = conn.cursor()
    try:
        # Steps
        cursor.execute(
            "SELECT SUM(count) FROM steps_record_table WHERE start_time >= ? AND start_time <= ?",
            (start_ms, end_ms),
        )
        steps_total = cursor.fetchone()[0] or 0

        # Distance (meters)
        cursor.execute(
            "SELECT SUM(distance) FROM distance_record_table WHERE start_time >= ? AND start_time <= ?",
            (start_ms, end_ms),
        )
        distance_meters = cursor.fetchone()[0] or 0.0
        distance_miles = round(distance_meters * 0.000621371, 2)
        distance_km = round(distance_meters / 1000.0, 2)

        # Calories (Stored in Joules in Health Connect standard schema; 1 kcal = 4184 J)
        cursor.execute(
            "SELECT SUM(energy) FROM total_calories_burned_record_table WHERE start_time >= ? AND start_time <= ?",
            (start_ms, end_ms),
        )
        total_energy_joules = cursor.fetchone()[0] or 0.0
        total_kcal = round(total_energy_joules / 4184.0, 1)

        # Active calories (Joules to kcal)
        cursor.execute(
            "SELECT SUM(energy) FROM active_calories_burned_record_table WHERE start_time >= ? AND start_time <= ?",
            (start_ms, end_ms),
        )
        active_energy_joules = cursor.fetchone()[0] or 0.0
        active_kcal = round(active_energy_joules / 4184.0, 1)

        # Resting Heart Rate
        cursor.execute(
            "SELECT beats_per_minute FROM resting_heart_rate_record_table WHERE time >= ? AND time <= ? ORDER BY time DESC LIMIT 1",
            (start_ms, end_ms),
        )
        rhr_row = cursor.fetchone()
        resting_hr = rhr_row[0] if rhr_row else None

        # Workouts
        cursor.execute(
            "SELECT exercise_type, title, start_time, end_time, session_rate_of_perceived_exertion FROM exercise_session_record_table WHERE start_time >= ? AND start_time <= ? ORDER BY start_time ASC",
            (start_ms, end_ms),
        )
        workouts = []
        for w in cursor.fetchall():
            dur_mins = round((w["end_time"] - w["start_time"]) / 60000.0, 1)
            type_name = EXERCISE_TYPE_MAP.get(w["exercise_type"], f"Exercise ({w['exercise_type']})")
            workouts.append({
                "type": type_name,
                "title": w["title"] or type_name,
                "duration_minutes": dur_mins,
                "start_time": datetime.fromtimestamp(w["start_time"] / 1000).strftime("%H:%M"),
            })

        # Sleep Session
        cursor.execute(
            "SELECT row_id, title, start_time, end_time FROM sleep_session_record_table WHERE end_time >= ? AND end_time <= ? ORDER BY end_time DESC LIMIT 1",
            (start_ms, end_ms + (4 * 3600 * 1000)),
        )
        sleep_row = cursor.fetchone()
        sleep_summary = None
        if sleep_row:
            dur_hours = round((sleep_row["end_time"] - sleep_row["start_time"]) / 3600000.0, 2)
            sleep_summary = {
                "duration_hours": dur_hours,
                "bedtime": datetime.fromtimestamp(sleep_row["start_time"] / 1000).strftime("%Y-%m-%d %H:%M"),
                "wake_time": datetime.fromtimestamp(sleep_row["end_time"] / 1000).strftime("%Y-%m-%d %H:%M"),
                "source": sleep_row["title"] or "Sleep Tracker",
            }

        conn.close()

        return {
            "status": "success",
            "source": "Health Connect Database (Local)",
            "date": resolved_date,
            "steps": int(steps_total),
            "distance": {
                "miles": distance_miles,
                "kilometers": distance_km,
            },
            "calories": {
                "total_kcal": total_kcal,
                "active_kcal": active_kcal,
            },
            "resting_heart_rate_bpm": resting_hr,
            "sleep": sleep_summary,
            "workouts": workouts,
        }

    except Exception as e:
        conn.close()
        return {"status": "error", "message": f"Error querying daily health summary: {e}"}


def get_sleep_breakdown(date_str: Optional[str] = None) -> dict:
    """Retrieve detailed sleep session and sleep stage breakdown (Awake, REM, Light, Deep).

    Fetches high-resolution hypnogram and contributor scores from Oura Cloud API.
    """
    start_ms, end_ms, resolved_date = _parse_date_bounds(date_str)
    start_date_q = (datetime.strptime(resolved_date, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")

    # Try live Oura API first
    try:
        sessions = oura_client.get_sleep_sessions(start_date=start_date_q, end_date=resolved_date)
        scores = oura_client.get_daily_sleep(start_date=start_date_q, end_date=resolved_date)

        target_session = None
        for s in reversed(sessions):
            if s.get("day") == resolved_date or s.get("bedtime_end", "").startswith(resolved_date):
                target_session = s
                break

        if not target_session and sessions:
            target_session = sessions[-1]

        if target_session:
            target_day = target_session.get("day")
            matching_score = next((sc for sc in scores if sc.get("day") == target_day), None)

            total_sec = target_session.get("total_sleep_duration", 0)
            deep_sec = target_session.get("deep_sleep_duration", 0)
            rem_sec = target_session.get("rem_sleep_duration", 0)
            light_sec = target_session.get("light_sleep_duration", 0)
            awake_sec = target_session.get("awake_time", 0)

            total_sleep_min = (deep_sec + rem_sec + light_sec) / 60.0

            return {
                "status": "success",
                "source": "Oura Ring API (Live)",
                "date": target_day,
                "score": matching_score.get("score") if matching_score else None,
                "bedtime_start": target_session.get("bedtime_start"),
                "bedtime_end": target_session.get("bedtime_end"),
                "total_time_in_bed_hours": round(target_session.get("time_in_bed", 0) / 3600.0, 2),
                "total_sleep_hours": round(total_sec / 3600.0, 2),
                "efficiency_percent": target_session.get("efficiency"),
                "resting_heart_rate_bpm": target_session.get("lowest_heart_rate") or target_session.get("average_heart_rate"),
                "average_hrv_rmssd_ms": target_session.get("average_hrv"),
                "average_breath_rate": target_session.get("average_breath"),
                "stages_minutes": {
                    "Deep Sleep": round(deep_sec / 60.0, 1),
                    "REM Sleep": round(rem_sec / 60.0, 1),
                    "Light Sleep": round(light_sec / 60.0, 1),
                    "Awake": round(awake_sec / 60.0, 1),
                },
                "stages_percentages": {
                    "Deep Sleep": f"{round((deep_sec / 60.0 / total_sleep_min) * 100, 1)}%" if total_sleep_min > 0 else "0%",
                    "REM Sleep": f"{round((rem_sec / 60.0 / total_sleep_min) * 100, 1)}%" if total_sleep_min > 0 else "0%",
                    "Light Sleep": f"{round((light_sec / 60.0 / total_sleep_min) * 100, 1)}%" if total_sleep_min > 0 else "0%",
                },
                "contributors": matching_score.get("contributors") if matching_score else {},
            }
    except Exception as e:
        print(f"[Health Manager] Oura sleep query error: {e}", flush=True)

    # Fallback to local DB
    conn = _get_connection()
    if not conn:
        return {"status": "error", "message": "Health database not found and Oura live query failed."}

    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT row_id, title, start_time, end_time FROM sleep_session_record_table WHERE end_time >= ? AND end_time <= ? ORDER BY (end_time - start_time) DESC LIMIT 1",
            (start_ms, end_ms + (4 * 3600 * 1000)),
        )
        session = cursor.fetchone()
        if not session:
            conn.close()
            return {
                "status": "success",
                "date": resolved_date,
                "message": f"No sleep session recorded ending on {resolved_date}.",
            }

        session_id = session["row_id"]
        total_duration_ms = session["end_time"] - session["start_time"]
        total_hours = round(total_duration_ms / 3600000.0, 2)

        cursor.execute(
            "SELECT stage_type, stage_start_time, stage_end_time FROM sleep_stages_table WHERE parent_key = ? ORDER BY stage_start_time ASC",
            (session_id,),
        )
        stages = cursor.fetchall()

        stage_totals = {"Deep Sleep": 0.0, "Light Sleep": 0.0, "REM Sleep": 0.0, "Awake": 0.0, "Other": 0.0}
        for st in stages:
            dur_min = (st["stage_end_time"] - st["stage_start_time"]) / 60000.0
            name = SLEEP_STAGE_MAP.get(st["stage_type"], "Other")
            if name in stage_totals:
                stage_totals[name] += dur_min
            else:
                stage_totals["Other"] += dur_min

        conn.close()

        total_sleep_min = stage_totals["Deep Sleep"] + stage_totals["Light Sleep"] + stage_totals["REM Sleep"]

        return {
            "status": "success",
            "source": "Health Connect Database (Local)",
            "date": resolved_date,
            "source_app": session["title"] or "Sleep Tracker",
            "bedtime": datetime.fromtimestamp(session["start_time"] / 1000).strftime("%Y-%m-%d %H:%M"),
            "wake_time": datetime.fromtimestamp(session["end_time"] / 1000).strftime("%Y-%m-%d %H:%M"),
            "total_time_in_bed_hours": total_hours,
            "total_sleep_hours": round(total_sleep_min / 60.0, 2),
            "stages_minutes": {k: round(v, 1) for k, v in stage_totals.items() if v > 0},
            "stages_percentages": {
                k: f"{round((v / total_sleep_min) * 100, 1)}%"
                for k, v in stage_totals.items()
                if total_sleep_min > 0 and k != "Awake" and v > 0
            },
        }
    except Exception as e:
        conn.close()
        return {"status": "error", "message": f"Error querying sleep breakdown: {e}"}


def get_readiness_summary(date_str: Optional[str] = None) -> dict:
    """Retrieve Oura Daily Readiness score and recovery contributors."""
    _, _, resolved_date = _parse_date_bounds(date_str)
    start_date_q = (datetime.strptime(resolved_date, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")

    try:
        readiness_list = oura_client.get_daily_readiness(start_date=start_date_q, end_date=resolved_date)
        target = next((r for r in reversed(readiness_list) if r.get("day") == resolved_date), None)
        if not target and readiness_list:
            target = readiness_list[-1]

        if target:
            return {
                "status": "success",
                "source": "Oura Ring API (Live)",
                "date": target.get("day"),
                "readiness_score": target.get("score"),
                "temperature_deviation_c": target.get("temperature_deviation"),
                "temperature_trend_deviation_c": target.get("temperature_trend_deviation"),
                "contributors": target.get("contributors", {}),
            }
        return {"status": "error", "message": f"No readiness data found for {resolved_date}."}
    except Exception as e:
        return {"status": "error", "message": f"Error querying readiness: {e}"}


def get_stress_summary(date_str: Optional[str] = None) -> dict:
    """Retrieve Oura Daytime Stress and recovery periods."""
    _, _, resolved_date = _parse_date_bounds(date_str)
    start_date_q = (datetime.strptime(resolved_date, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")

    try:
        stress_list = oura_client.get_daily_stress(start_date=start_date_q, end_date=resolved_date)
        target = next((s for s in reversed(stress_list) if s.get("day") == resolved_date), None)
        if not target and stress_list:
            target = stress_list[-1]

        if target:
            return {
                "status": "success",
                "source": "Oura Ring API (Live)",
                "date": target.get("day"),
                "day_summary": target.get("day_summary"),
                "stress_duration_minutes": round(target.get("stress_high", 0) / 60.0, 1),
                "recovery_duration_minutes": round(target.get("recovery_high", 0) / 60.0, 1),
            }
        return {"status": "error", "message": f"No stress data found for {resolved_date}."}
    except Exception as e:
        return {"status": "error", "message": f"Error querying stress: {e}"}


def get_recent_workouts(days: int = 7) -> list:
    """Retrieve recorded workout and exercise sessions for the past N days."""
    conn = _get_connection()
    if not conn:
        return []

    cutoff_dt = datetime.now() - timedelta(days=days)
    cutoff_ms = int(cutoff_dt.timestamp() * 1000)

    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT row_id, exercise_type, title, notes, start_time, end_time FROM exercise_session_record_table WHERE start_time >= ? ORDER BY start_time DESC",
            (cutoff_ms,),
        )
        results = []
        for r in cursor.fetchall():
            dur_mins = round((r["end_time"] - r["start_time"]) / 60000.0, 1)
            type_name = EXERCISE_TYPE_MAP.get(r["exercise_type"], f"Exercise ({r['exercise_type']})")
            results.append({
                "type": type_name,
                "title": r["title"] or type_name,
                "notes": r["notes"],
                "duration_minutes": dur_mins,
                "date": datetime.fromtimestamp(r["start_time"] / 1000).strftime("%Y-%m-%d %H:%M"),
            })
        conn.close()
        return results
    except Exception as e:
        conn.close()
        print(f"[Health Manager] Error getting recent workouts: {e}", flush=True)
        return []


def get_vitals_trend(metric: str = "resting_heart_rate", days: int = 14) -> list:
    """Retrieve historical vitals trends (resting HR or HRV)."""
    conn = _get_connection()
    if not conn:
        return []

    cutoff_dt = datetime.now() - timedelta(days=days)
    cutoff_ms = int(cutoff_dt.timestamp() * 1000)
    cursor = conn.cursor()

    results = []
    try:
        if metric in ("resting_heart_rate", "rhr"):
            cursor.execute(
                "SELECT time, beats_per_minute FROM resting_heart_rate_record_table WHERE time >= ? ORDER BY time ASC",
                (cutoff_ms,),
            )
            for r in cursor.fetchall():
                results.append({
                    "date": datetime.fromtimestamp(r["time"] / 1000).strftime("%Y-%m-%d"),
                    "resting_hr_bpm": r["beats_per_minute"],
                })
        elif metric in ("hrv", "heart_rate_variability"):
            cursor.execute(
                "SELECT time, heart_rate_variability_millis FROM heart_rate_variability_rmssd_record_table WHERE time >= ? ORDER BY time ASC",
                (cutoff_ms,),
            )
            for r in cursor.fetchall():
                results.append({
                    "date": datetime.fromtimestamp(r["time"] / 1000).strftime("%Y-%m-%d"),
                    "hrv_rmssd_ms": round(r["heart_rate_variability_millis"], 1),
                })
        conn.close()
        return results
    except Exception as e:
        conn.close()
        print(f"[Health Manager] Error getting vitals trend: {e}", flush=True)
        return []


def get_clinical_records(limit: int = 10) -> list:
    """Retrieve FHIR clinical records (Lab observations, vitals, patient info)."""
    conn = _get_connection()
    if not conn:
        return []

    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT fhir_resource_type, fhir_data, last_modified_time FROM medical_resource_table ORDER BY last_modified_time DESC LIMIT ?",
            (limit,),
        )
        records = []
        for r in cursor.fetchall():
            try:
                data = json.loads(r["fhir_data"])
                records.append({
                    "resource_type": data.get("resourceType"),
                    "id": data.get("id"),
                    "status": data.get("status"),
                    "effective_date": data.get("effectiveDateTime"),
                    "data": data,
                })
            except Exception:
                pass
        conn.close()
        return records
    except Exception as e:
        conn.close()
        print(f"[Health Manager] Error getting clinical records: {e}", flush=True)
        return []
