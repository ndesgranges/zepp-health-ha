#!/usr/bin/env python3
"""
Zepp Health Data Extractor
Extracts health metrics from Zepp (Huami/Amazfit) on-device SQLite databases.

Databases are pulled from:
    adb pull /data/data/com.huami.watch.hmwatchmanager/databases ./zepp-databases

Outputs a single JSON file ready to be consumed by the Zepp Health HA integration.

Usage:
    python3 extract_zepp_health.py --db-dir ./zepp-databases --output zepp_health_export.json
"""

import argparse
import glob
import json
import sqlite3
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- Configuration ---
# These can be overridden via --db-dir. Defaults to script's parent directory.
DB_DIR: Path = Path(__file__).parent

# Database file patterns (auto-discovered by glob)
# The IDs in filenames are user/device-specific, so we match by prefix.
DB_HEALTH_MATRIX: str | None = None  # HealthMatrix_*.db
DB_COMPANION = "companion-aa.db"
DB_REST_HR = "RestHeartDb"
DB_PHNDATA: str | None = None  # phndata-*.db
DB_SLEEP = "sleep_db"

# Sleep stage mode mapping (from Zepp algo)
SLEEP_MODES = {
    4: "light",
    5: "deep",
    7: "awake",
    8: "rem",
}


def _find_db(pattern: str) -> str | None:
    """Find a database file matching a glob pattern in DB_DIR."""
    matches = sorted(
        glob.glob(str(DB_DIR / pattern)),
        key=os.path.getsize,
        reverse=True,  # prefer largest file (most data)
    )
    # Exclude WAL/SHM/journal files
    matches = [m for m in matches if not any(m.endswith(s) for s in ("-wal", "-shm", "-journal"))]
    if matches:
        return os.path.basename(matches[0])
    return None


def _discover_databases():
    """Auto-discover device-specific database filenames."""
    # pylint: disable=global-statement
    global DB_HEALTH_MATRIX, DB_PHNDATA

    DB_HEALTH_MATRIX = _find_db("HealthMatrix_*.db")
    if DB_HEALTH_MATRIX:
        print(f"  Found HealthMatrix: {DB_HEALTH_MATRIX}")
    else:
        print("  [WARN] No HealthMatrix_*.db found")

    DB_PHNDATA = _find_db("phndata-*.db")
    if DB_PHNDATA:
        print(f"  Found phndata: {DB_PHNDATA}")
    else:
        print("  [WARN] No phndata-*.db found")


def db_path(filename):
    """Get absolute path to a database file."""
    return str(DB_DIR / filename)


def safe_connect(filename):
    """Connect to a database file if it exists, else return None."""
    if filename is None:
        print("  [SKIP] database not found during discovery")
        return None
    path = db_path(filename)
    if not os.path.exists(path):
        print(f"  [SKIP] {filename} not found")
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"  [ERROR] {filename}: {e}")
        return None


# ============================================================
# EXTRACTION FUNCTIONS
# ============================================================


def extract_training_load():
    """
    Extract ATL (Acute Training Load / Fatigue), CTL (Chronic Training Load / Form),
    TSB (Training Stress Balance) from phndata database.
    """
    print("Extracting training load (ATL/CTL/TSB)...")
    conn = safe_connect(DB_PHNDATA)
    if not conn:
        return []

    rows = conn.execute("""
        SELECT timestamp, atl, ctl, tsb, atlTotal, ctlTotal, tsbTTotal,
               totalScore, completionPercent, activityScore, exerciseScore,
               recoveryFactor, recoveryFactorID, targetScore
        FROM exertion_daily_algo_result
        ORDER BY timestamp ASC
    """).fetchall()

    results = []
    for r in rows:
        ts = r["timestamp"] / 1000  # ms -> seconds
        results.append({
            "date": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"),
            "timestamp": int(r["timestamp"]),
            "atl": r["atl"],
            "ctl": r["ctl"],
            "tsb": r["tsb"],
            "atl_total": r["atlTotal"],
            "ctl_total": r["ctlTotal"],
            "tsb_total": r["tsbTTotal"],
            "daily_score": r["totalScore"],
            "target_score": r["targetScore"],
            "completion_pct": r["completionPercent"],
            "activity_score": r["activityScore"],
            "exercise_score": r["exerciseScore"],
            "recovery_factor": r["recoveryFactor"],
            "recovery_factor_id": r["recoveryFactorID"],
        })

    conn.close()
    print(f"  -> {len(results)} days of training load data")
    return results


def extract_sport_load():
    """
    Extract daily and weekly training load from companion database.
    """
    print("Extracting sport load data...")
    conn = safe_connect(DB_COMPANION)
    if not conn:
        return []

    rows = conn.execute("""
        SELECT date, dailyTL, weeklyTLSum, weeklyTLSumOptimalMin,
               weeklyTLSumOptimalMax, weeklyTLSumOverreaching
        FROM sport_load_data
        ORDER BY date ASC
    """).fetchall()

    results = []
    for r in rows:
        results.append({
            "date": r["date"],
            "daily_training_load": r["dailyTL"],
            "weekly_training_load": r["weeklyTLSum"],
            "weekly_optimal_min": r["weeklyTLSumOptimalMin"],
            "weekly_optimal_max": r["weeklyTLSumOptimalMax"],
            "weekly_overreaching": r["weeklyTLSumOverreaching"],
        })

    conn.close()
    print(f"  -> {len(results)} days of sport load data")
    return results


def extract_daily_readiness():
    """
    Extract daily readiness/biocharge data from HealthMatrix (sampleType 17002).
    Includes: wakeCharge (biocharge at wake), exertionScore, dailyFitnessScore,
    stressFitnessScore, mentalWake, physicalWake.
    """
    print("Extracting daily readiness (biocharge/wake)...")
    conn = safe_connect(DB_HEALTH_MATRIX)
    if not conn:
        return []

    # Get all daily readiness entries (sampleType 17002)
    rows = conn.execute("""
        SELECT s.dataId, s.startDate, s.endDate, q.quantity
        FROM Samples s
        LEFT JOIN QuantitySamples q ON s.dataId = q.dataId
        WHERE s.sampleType = 17002
        ORDER BY s.startDate ASC
    """).fetchall()

    # For each entry, fetch extra data
    results = []
    for r in rows:
        data_id = r["dataId"]
        extras = conn.execute("""
            SELECT ek.key, ev.numericalValue, ev.stringValue
            FROM ExtraDataValues ev
            JOIN ExtraDataKeys ek ON ev.keyId = ek.keyId
            WHERE ev.objectId = ?
        """, (data_id,)).fetchall()

        ts_sec = r["startDate"] / 1000  # ms -> seconds
        entry = {
            "date": datetime.fromtimestamp(ts_sec, tz=timezone.utc).strftime("%Y-%m-%d"),
            "timestamp": r["startDate"],
            "biocharge_score": r["quantity"],
        }
        for ex in extras:
            key = ex["key"]
            val = ex["numericalValue"] if ex["numericalValue"] is not None else ex["stringValue"]
            if key in ("dailyFitnessScore", "exertionScore", "stressFitnessScore",
                       "wakeCharge", "mentalWake", "physicalWake"):
                entry[key] = val

        # Rename for clarity
        entry["wake_biocharge"] = entry.pop("wakeCharge", None)
        entry["daily_fitness_score"] = entry.pop("dailyFitnessScore", None)
        entry["exertion_score"] = entry.pop("exertionScore", None)
        entry["stress_fitness_score"] = entry.pop("stressFitnessScore", None)
        entry["mental_wake"] = entry.pop("mentalWake", None)
        entry["physical_wake"] = entry.pop("physicalWake", None)

        results.append(entry)

    conn.close()
    print(f"  -> {len(results)} days of readiness data")
    return results


def extract_biocharge_daily():
    """
    Extract daily biocharge summary (sampleType 17005): max/min charge, energy consumed.
    """
    print("Extracting daily biocharge summary...")
    conn = safe_connect(DB_HEALTH_MATRIX)
    if not conn:
        return []

    rows = conn.execute("""
        SELECT s.dataId, s.startDate, q.quantity
        FROM Samples s
        LEFT JOIN QuantitySamples q ON s.dataId = q.dataId
        WHERE s.sampleType = 17005
        ORDER BY s.startDate ASC
    """).fetchall()

    results = []
    for r in rows:
        data_id = r["dataId"]
        extras = conn.execute("""
            SELECT ek.key, ev.numericalValue
            FROM ExtraDataValues ev
            JOIN ExtraDataKeys ek ON ev.keyId = ek.keyId
            WHERE ev.objectId = ?
        """, (data_id,)).fetchall()

        ts_sec = r["startDate"] / 1000
        entry = {
            "date": datetime.fromtimestamp(ts_sec, tz=timezone.utc).strftime("%Y-%m-%d"),
            "timestamp": r["startDate"],
            "biocharge_range": r["quantity"],  # difference max-min
        }
        for ex in extras:
            if ex["key"] == "cumulativeConsumptionEnergy":
                entry["energy_consumed"] = ex["numericalValue"]
            elif ex["key"] == "maxCharge":
                entry["max_biocharge"] = ex["numericalValue"]
            elif ex["key"] == "minCharge":
                entry["min_biocharge"] = ex["numericalValue"]

        results.append(entry)

    conn.close()
    print(f"  -> {len(results)} days of biocharge summary")
    return results


def extract_rest_heart_rate():
    """
    Extract resting heart rate from RestHeartDb.
    """
    print("Extracting resting heart rate...")
    conn = safe_connect(DB_REST_HR)
    if not conn:
        return []

    rows = conn.execute("""
        SELECT date, heartRate, timestamp, deviceType
        FROM rest_heart_rate
        ORDER BY date ASC
    """).fetchall()

    results = []
    for r in rows:
        results.append({
            "date": r["date"],
            "resting_hr": r["heartRate"],
            "timestamp": r["timestamp"],
        })

    conn.close()
    print(f"  -> {len(results)} days of resting HR")
    return results


def _parse_sleep_stages(stages):
    """Parse sleep stage list into duration totals by type."""
    mins = {"deep": 0, "light": 0, "rem": 0, "awake": 0}
    for stage in stages:
        duration = stage["stop"] - stage["start"] + 1
        mode = stage["mode"]
        if mode == 5:
            mins["deep"] += duration
        elif mode == 4:
            mins["light"] += duration
        elif mode == 8:
            mins["rem"] += duration
        elif mode == 7:
            mins["awake"] += duration
    return mins


def extract_sleep_data():
    """
    Extract sleep data from companion-aa.db health_data_did JSON.
    Includes: total sleep time, deep/light/REM/awake minutes, sleep score,
    sleep HR, sleep regularity indicators.
    """
    print("Extracting sleep data...")
    conn = safe_connect(DB_COMPANION)
    if not conn:
        return []

    rows = conn.execute("""
        SELECT date, summary FROM health_data_did
        ORDER BY date ASC
    """).fetchall()

    results = []
    for r in rows:
        try:
            data = json.loads(r["summary"])
        except (json.JSONDecodeError, TypeError):
            continue

        slp = data.get("slp")
        if not slp:
            continue

        stage_mins = _parse_sleep_stages(slp.get("stage", []))

        sleep_start = slp.get("st")  # unix timestamp
        sleep_end = slp.get("ed")  # unix timestamp

        entry = {
            "date": r["date"],
            "total_sleep_minutes": (
                stage_mins["deep"] + stage_mins["light"] + stage_mins["rem"]
            ),
            "deep_sleep_minutes": stage_mins["deep"],
            "light_sleep_minutes": stage_mins["light"],
            "rem_sleep_minutes": stage_mins["rem"],
            "awake_minutes": stage_mins["awake"],
            "sleep_score": slp.get("ss"),
            "sleep_hr": slp.get("rhr"),  # resting HR during sleep
            "sleep_start": (
                datetime.fromtimestamp(sleep_start, tz=timezone.utc).isoformat()
                if sleep_start else None
            ),
            "sleep_end": (
                datetime.fromtimestamp(sleep_end, tz=timezone.utc).isoformat()
                if sleep_end else None
            ),
            "into_sleep_latency": slp.get("is"),  # minutes to fall asleep
            "wake_count": slp.get("wc", 0),
        }

        # Calculate sleep regularity: bedtime as minutes-from-midnight
        if sleep_start:
            dt = datetime.fromtimestamp(sleep_start, tz=timezone(timedelta(seconds=3600)))
            bedtime_minutes = dt.hour * 60 + dt.minute
            # Normalize: if after midnight, add 1440 to keep ordering
            if bedtime_minutes < 720:  # before noon = after midnight sleep
                bedtime_minutes += 1440
            entry["bedtime_minutes_from_midnight"] = bedtime_minutes

        results.append(entry)

    conn.close()
    print(f"  -> {len(results)} nights of sleep data")
    return results


def extract_hrv_data():
    """
    Extract HRV data from HealthMatrix (sampleType 12002 = daily HRV RMSSD).
    """
    print("Extracting HRV data...")
    conn = safe_connect(DB_HEALTH_MATRIX)
    if not conn:
        return []

    rows = conn.execute("""
        SELECT s.startDate, q.quantity
        FROM Samples s
        JOIN QuantitySamples q ON s.dataId = q.dataId
        WHERE s.sampleType = 12002
        ORDER BY s.startDate ASC
    """).fetchall()

    results = []
    for r in rows:
        ts_sec = r["startDate"] / 1000
        results.append({
            "date": datetime.fromtimestamp(ts_sec, tz=timezone.utc).strftime("%Y-%m-%d"),
            "timestamp": r["startDate"],
            "hrv_rmssd": r["quantity"],
        })

    conn.close()
    print(f"  -> {len(results)} days of HRV data")
    return results


def extract_pai_data():
    """
    Extract PAI (Personal Activity Intelligence) from HealthMatrix (sampleType 8001/8002).
    8001 = daily PAI, 8002 = weekly/total PAI.
    """
    print("Extracting PAI data...")
    conn = safe_connect(DB_HEALTH_MATRIX)
    if not conn:
        return []

    # Daily PAI
    rows = conn.execute("""
        SELECT s.startDate, q.quantity
        FROM Samples s
        JOIN QuantitySamples q ON s.dataId = q.dataId
        WHERE s.sampleType = 8001
        ORDER BY s.startDate ASC
    """).fetchall()

    daily_pai = {}
    for r in rows:
        date = datetime.fromtimestamp(r["startDate"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        daily_pai[date] = r["quantity"]

    # Weekly PAI
    rows = conn.execute("""
        SELECT s.startDate, q.quantity
        FROM Samples s
        JOIN QuantitySamples q ON s.dataId = q.dataId
        WHERE s.sampleType = 8002
        ORDER BY s.startDate ASC
    """).fetchall()

    weekly_pai = {}
    for r in rows:
        date = datetime.fromtimestamp(r["startDate"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        weekly_pai[date] = r["quantity"]

    # Merge
    all_dates = sorted(set(list(daily_pai.keys()) + list(weekly_pai.keys())))
    results = []
    for date in all_dates:
        results.append({
            "date": date,
            "daily_pai": daily_pai.get(date),
            "weekly_pai": weekly_pai.get(date),
        })

    conn.close()
    print(f"  -> {len(results)} days of PAI data")
    return results


def extract_spo2_data():
    """
    Extract SpO2/blood oxygen data from HealthMatrix (sampleType 4004).
    """
    print("Extracting SpO2 data...")
    conn = safe_connect(DB_HEALTH_MATRIX)
    if not conn:
        return []

    rows = conn.execute("""
        SELECT s.dataId, s.startDate, q.quantity
        FROM Samples s
        LEFT JOIN QuantitySamples q ON s.dataId = q.dataId
        WHERE s.sampleType = 4004
        ORDER BY s.startDate ASC
    """).fetchall()

    results = []
    for r in rows:
        data_id = r["dataId"]
        extras = conn.execute("""
            SELECT ek.key, ev.numericalValue
            FROM ExtraDataValues ev
            JOIN ExtraDataKeys ek ON ev.keyId = ek.keyId
            WHERE ev.objectId = ?
        """, (data_id,)).fetchall()

        ts_sec = r["startDate"] / 1000
        entry = {
            "date": datetime.fromtimestamp(ts_sec, tz=timezone.utc).strftime("%Y-%m-%d"),
            "timestamp": r["startDate"],
            "odi_percentage": r["quantity"],  # Oxygen Desaturation Index
        }
        for ex in extras:
            if ex["key"] == "score":
                entry["spo2_score"] = ex["numericalValue"]
            elif ex["key"] == "odiNum":
                entry["odi_count"] = ex["numericalValue"]

        results.append(entry)

    conn.close()
    print(f"  -> {len(results)} days of SpO2 data")
    return results


def extract_daily_steps():
    """
    Extract daily steps from companion health_data_did.
    """
    print("Extracting daily steps...")
    conn = safe_connect(DB_COMPANION)
    if not conn:
        return []

    rows = conn.execute("""
        SELECT date, summary FROM health_data_did
        ORDER BY date ASC
    """).fetchall()

    results = []
    for r in rows:
        try:
            data = json.loads(r["summary"])
        except (json.JSONDecodeError, TypeError):
            continue

        stp = data.get("stp")
        if not stp:
            continue

        results.append({
            "date": r["date"],
            "steps": stp.get("ttl", 0),
            "distance_meters": stp.get("dis", 0),
            "calories": stp.get("cal", 0),
            "walking_minutes": stp.get("wk", 0),
            "running_minutes": stp.get("rn", 0),
            "run_distance": stp.get("runDist", 0),
        })

    conn.close()
    print(f"  -> {len(results)} days of step data")
    return results


def extract_readiness_components():
    """
    Extract readiness score components (sampleType 5001-5008):
    5001 = overall readiness score
    5002 = sleep component
    5003 = HRV component
    5004 = RHR component
    5005 = SpO2 component (blood oxygen)
    5006 = recovery component
    5007 = sleep regularity
    5008 = previous day activity
    """
    print("Extracting readiness components...")
    conn = safe_connect(DB_HEALTH_MATRIX)
    if not conn:
        return []

    component_names = {
        5001: "readiness_score",
        5002: "sleep_component",
        5003: "hrv_component",
        5004: "rhr_component",
        5005: "spo2_component",
        5006: "recovery_component",
        5007: "sleep_regularity",
        5008: "activity_component",
    }

    rows = conn.execute("""
        SELECT s.dataId, s.sampleType, s.startDate, q.quantity
        FROM Samples s
        LEFT JOIN QuantitySamples q ON s.dataId = q.dataId
        WHERE s.sampleType BETWEEN 5001 AND 5008
        ORDER BY s.startDate ASC
    """).fetchall()

    # Group by date
    by_date = {}
    for r in rows:
        date = datetime.fromtimestamp(r["startDate"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        if date not in by_date:
            by_date[date] = {"date": date, "timestamp": r["startDate"]}
        component_key = component_names.get(r["sampleType"])
        if component_key and r["quantity"] is not None and r["quantity"] != 255:
            by_date[date][component_key] = r["quantity"]

    results = list(by_date.values())
    conn.close()
    print(f"  -> {len(results)} days of readiness components")
    return results


def compute_sleep_regularity(sleep_data):
    """
    Compute sleep regularity metrics from sleep data.
    Returns the sleep data enriched with regularity scores.
    """
    if len(sleep_data) < 2:
        return sleep_data

    # Compute 7-day rolling std dev of bedtime as regularity indicator
    for i, entry in enumerate(sleep_data):
        if i < 6:
            continue  # need at least 7 days

        window = sleep_data[max(0, i - 6):i + 1]
        bedtimes = [d.get("bedtime_minutes_from_midnight") for d in window
                    if d.get("bedtime_minutes_from_midnight") is not None]

        if len(bedtimes) >= 3:
            mean_bt = sum(bedtimes) / len(bedtimes)
            variance = sum((bt - mean_bt) ** 2 for bt in bedtimes) / len(bedtimes)
            std_dev = variance ** 0.5
            # Regularity score: lower std dev = more regular (0-100 scale)
            # 0 min std = 100, 120 min std = 0
            regularity = max(0, min(100, 100 - (std_dev / 120) * 100))
            entry["sleep_regularity_score"] = round(regularity, 1)
            entry["bedtime_std_dev_minutes"] = round(std_dev, 1)

    return sleep_data


# ============================================================
# MAIN
# ============================================================


def _extract_all_metrics():
    """Run all extraction functions and return combined data dict."""
    training_load = extract_training_load()
    sport_load = extract_sport_load()
    readiness = extract_daily_readiness()
    biocharge = extract_biocharge_daily()
    rest_hr = extract_rest_heart_rate()
    sleep = compute_sleep_regularity(extract_sleep_data())
    hrv = extract_hrv_data()
    pai = extract_pai_data()
    spo2 = extract_spo2_data()
    steps = extract_daily_steps()
    readiness_components = extract_readiness_components()

    return {
        "training_load": training_load,
        "sport_load": sport_load,
        "daily_readiness": readiness,
        "biocharge_daily": biocharge,
        "resting_heart_rate": rest_hr,
        "sleep": sleep,
        "hrv": hrv,
        "pai": pai,
        "spo2": spo2,
        "steps": steps,
        "readiness_components": readiness_components,
    }


def _compute_latest(data):
    """Compute latest values from extracted data for HA sensors."""
    latest = {}

    if data["training_load"]:
        last = data["training_load"][-1]
        latest.update({
            "atl": last["atl"], "ctl": last["ctl"], "tsb": last["tsb"],
            "atl_total": last["atl_total"], "ctl_total": last["ctl_total"],
            "tsb_total": last["tsb_total"],
            "recovery_factor": last["recovery_factor"],
            "training_load_date": last["date"],
        })

    if data["sport_load"]:
        last = data["sport_load"][-1]
        latest.update({
            "daily_training_load": last["daily_training_load"],
            "weekly_training_load": last["weekly_training_load"],
            "weekly_optimal_min": last["weekly_optimal_min"],
            "weekly_optimal_max": last["weekly_optimal_max"],
        })

    if data["daily_readiness"]:
        last = data["daily_readiness"][-1]
        latest.update({
            "biocharge_score": last.get("biocharge_score"),
            "wake_biocharge": last.get("wake_biocharge"),
            "exertion_score": last.get("exertion_score"),
            "mental_wake": last.get("mental_wake"),
            "physical_wake": last.get("physical_wake"),
        })

    if data["biocharge_daily"]:
        last = data["biocharge_daily"][-1]
        latest.update({
            "max_biocharge": last.get("max_biocharge"),
            "min_biocharge": last.get("min_biocharge"),
        })

    if data["resting_heart_rate"]:
        last = data["resting_heart_rate"][-1]
        latest["resting_hr"] = last["resting_hr"]
        latest["resting_hr_date"] = last["date"]

    if data["sleep"]:
        last = data["sleep"][-1]
        latest.update({
            "total_sleep_minutes": last["total_sleep_minutes"],
            "deep_sleep_minutes": last["deep_sleep_minutes"],
            "light_sleep_minutes": last["light_sleep_minutes"],
            "rem_sleep_minutes": last["rem_sleep_minutes"],
            "sleep_score": last.get("sleep_score"),
            "sleep_hr": last.get("sleep_hr"),
            "sleep_regularity_score": last.get("sleep_regularity_score"),
        })

    if data["hrv"]:
        last = data["hrv"][-1]
        latest["hrv_rmssd"] = last["hrv_rmssd"]
        latest["hrv_date"] = last["date"]

    if data["pai"]:
        last = data["pai"][-1]
        latest["daily_pai"] = last.get("daily_pai")
        latest["weekly_pai"] = last.get("weekly_pai")

    if data["readiness_components"]:
        last = data["readiness_components"][-1]
        latest["readiness_score"] = last.get("readiness_score")
        latest["readiness_date"] = last.get("date")

    return latest


def main():
    """Main entry point for the Zepp health data extraction."""
    # pylint: disable=global-statement
    global DB_DIR

    parser = argparse.ArgumentParser(
        description="Extract health data from Zepp watch databases."
    )
    parser.add_argument(
        "--db-dir",
        type=Path,
        default=Path(__file__).parent,
        help="Directory containing the pulled Zepp SQLite databases",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output JSON file path (default: <db-dir>/zepp_health_export.json)",
    )
    args = parser.parse_args()

    DB_DIR = args.db_dir.resolve()
    output_file = args.output or (DB_DIR / "zepp_health_export.json")

    print("=" * 60)
    print("Zepp Health Data Extractor")
    print("=" * 60)
    print(f"Database directory: {DB_DIR}")
    print(f"Output file: {output_file}")
    print()

    print("Discovering databases...")
    _discover_databases()
    print()

    data = _extract_all_metrics()
    latest = _compute_latest(data)

    export = {
        "exported_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": "zepp_databases",
        "data": data,
        "latest": latest,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2, ensure_ascii=False, default=str)

    print()
    print("=" * 60)
    print(f"Export complete: {output_file}")
    print(f"File size: {output_file.stat().st_size / 1024:.1f} KB")
    print()
    print("Latest values summary:")
    print("-" * 40)
    for key, val in latest.items():
        print(f"  {key}: {val}")
    print("=" * 60)


if __name__ == "__main__":
    main()
