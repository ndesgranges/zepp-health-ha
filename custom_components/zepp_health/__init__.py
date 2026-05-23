"""Zepp Health Import - Main integration setup.

Imports all historical Zepp health data into Home Assistant's
long-term statistics database so it's visible in history graphs.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from homeassistant.components.file_upload import FileUploadData
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.selector import FileSelector, FileSelectorConfig
import voluptuous as vol

from .const import DOMAIN, STORAGE_DIR, EXPORT_FILENAME

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]

# Define all sensor metrics and their units/metadata
SENSOR_DEFINITIONS = {
    # Training Load (ATL/CTL/TSB)
    "atl": {
        "name": "Acute Training Load (ATL)",
        "unit": None, "icon": "mdi:lightning-bolt",
        "category": "training_load", "field": "atl",
    },
    "ctl": {
        "name": "Chronic Training Load (CTL)",
        "unit": None, "icon": "mdi:chart-line",
        "category": "training_load", "field": "ctl",
    },
    "tsb": {
        "name": "Training Stress Balance (TSB)",
        "unit": None, "icon": "mdi:scale-balance",
        "category": "training_load", "field": "tsb",
    },
    "atl_total": {
        "name": "ATL Total",
        "unit": None, "icon": "mdi:lightning-bolt",
        "category": "training_load", "field": "atl_total",
    },
    "ctl_total": {
        "name": "CTL Total",
        "unit": None, "icon": "mdi:chart-line",
        "category": "training_load", "field": "ctl_total",
    },
    "tsb_total": {
        "name": "TSB Total",
        "unit": None, "icon": "mdi:scale-balance",
        "category": "training_load", "field": "tsb_total",
    },
    "daily_score": {
        "name": "Daily Training Score",
        "unit": None, "icon": "mdi:run",
        "category": "training_load", "field": "daily_score",
    },
    "target_score": {
        "name": "Target Training Score",
        "unit": None, "icon": "mdi:target",
        "category": "training_load", "field": "target_score",
    },

    # Sport Load
    "daily_training_load": {
        "name": "Daily Training Load",
        "unit": None, "icon": "mdi:dumbbell",
        "category": "sport_load", "field": "daily_training_load",
    },
    "weekly_training_load": {
        "name": "Weekly Training Load",
        "unit": None, "icon": "mdi:calendar-week",
        "category": "sport_load", "field": "weekly_training_load",
    },

    # Readiness / BioCharge
    "biocharge_score": {
        "name": "BioCharge Score",
        "unit": None, "icon": "mdi:battery-heart-variant",
        "category": "daily_readiness", "field": "biocharge_score",
    },
    "wake_biocharge": {
        "name": "Wake BioCharge",
        "unit": None, "icon": "mdi:weather-sunset-up",
        "category": "daily_readiness", "field": "wake_biocharge",
    },
    "exertion_score": {
        "name": "Exertion Score",
        "unit": None, "icon": "mdi:fire",
        "category": "daily_readiness", "field": "exertion_score",
    },
    "mental_wake": {
        "name": "Mental Wake",
        "unit": None, "icon": "mdi:head-lightbulb",
        "category": "daily_readiness", "field": "mental_wake",
    },
    "physical_wake": {
        "name": "Physical Wake",
        "unit": None, "icon": "mdi:arm-flex",
        "category": "daily_readiness", "field": "physical_wake",
    },
    "daily_fitness_score": {
        "name": "Daily Fitness Score",
        "unit": None, "icon": "mdi:heart-pulse",
        "category": "daily_readiness", "field": "daily_fitness_score",
    },
    "stress_fitness_score": {
        "name": "Stress Fitness Score",
        "unit": None, "icon": "mdi:meditation",
        "category": "daily_readiness", "field": "stress_fitness_score",
    },

    # BioCharge Daily
    "max_biocharge": {
        "name": "Max BioCharge",
        "unit": None, "icon": "mdi:battery-arrow-up",
        "category": "biocharge_daily", "field": "max_biocharge",
    },
    "min_biocharge": {
        "name": "Min BioCharge",
        "unit": None, "icon": "mdi:battery-arrow-down",
        "category": "biocharge_daily", "field": "min_biocharge",
    },
    "energy_consumed": {
        "name": "Energy Consumed",
        "unit": None, "icon": "mdi:flash",
        "category": "biocharge_daily", "field": "energy_consumed",
    },

    # Sleep
    "total_sleep_minutes": {
        "name": "Total Sleep",
        "unit": UnitOfTime.MINUTES, "icon": "mdi:sleep",
        "category": "sleep", "field": "total_sleep_minutes",
    },
    "deep_sleep_minutes": {
        "name": "Deep Sleep",
        "unit": UnitOfTime.MINUTES, "icon": "mdi:bed",
        "category": "sleep", "field": "deep_sleep_minutes",
    },
    "light_sleep_minutes": {
        "name": "Light Sleep",
        "unit": UnitOfTime.MINUTES, "icon": "mdi:bed-outline",
        "category": "sleep", "field": "light_sleep_minutes",
    },
    "rem_sleep_minutes": {
        "name": "REM Sleep",
        "unit": UnitOfTime.MINUTES, "icon": "mdi:eye-refresh",
        "category": "sleep", "field": "rem_sleep_minutes",
    },
    "awake_minutes": {
        "name": "Awake During Sleep",
        "unit": UnitOfTime.MINUTES, "icon": "mdi:eye-open",
        "category": "sleep", "field": "awake_minutes",
    },
    "sleep_score": {
        "name": "Sleep Score",
        "unit": None, "icon": "mdi:star-circle",
        "category": "sleep", "field": "sleep_score",
    },
    "sleep_hr": {
        "name": "Sleep Heart Rate",
        "unit": "bpm", "icon": "mdi:heart-pulse",
        "category": "sleep", "field": "sleep_hr",
    },
    "into_sleep_latency": {
        "name": "Sleep Latency",
        "unit": UnitOfTime.MINUTES, "icon": "mdi:timer-sand",
        "category": "sleep", "field": "into_sleep_latency",
    },
    "sleep_regularity_score": {
        "name": "Sleep Regularity",
        "unit": None, "icon": "mdi:clock-check",
        "category": "sleep", "field": "sleep_regularity_score",
    },

    # HRV
    "hrv_rmssd": {
        "name": "HRV (RMSSD)",
        "unit": "ms", "icon": "mdi:heart-flash",
        "category": "hrv", "field": "hrv_rmssd",
    },

    # Resting Heart Rate (from sleep data)
    "resting_hr": {
        "name": "Resting Heart Rate",
        "unit": "bpm", "icon": "mdi:heart",
        "category": "sleep", "field": "sleep_hr",
    },

    # PAI
    "daily_pai": {
        "name": "Daily PAI",
        "unit": None, "icon": "mdi:run-fast",
        "category": "pai", "field": "daily_pai",
    },
    "weekly_pai": {
        "name": "Weekly PAI",
        "unit": None, "icon": "mdi:calendar-heart",
        "category": "pai", "field": "weekly_pai",
    },

    # SpO2
    "spo2_score": {
        "name": "SpO2 Score",
        "unit": None, "icon": "mdi:lungs",
        "category": "spo2", "field": "spo2_score",
    },

    # Steps
    "steps": {
        "name": "Daily Steps",
        "unit": "steps", "icon": "mdi:walk",
        "category": "steps", "field": "steps",
    },
    "distance_meters": {
        "name": "Daily Distance",
        "unit": "m", "icon": "mdi:map-marker-distance",
        "category": "steps", "field": "distance_meters",
    },
    "calories": {
        "name": "Daily Calories",
        "unit": "kcal", "icon": "mdi:fire",
        "category": "steps", "field": "calories",
    },

    # Readiness Components
    "readiness_score": {
        "name": "Readiness Score",
        "unit": None, "icon": "mdi:gauge",
        "category": "readiness_components", "field": "readiness_score",
    },
    "sleep_component": {
        "name": "Readiness - Sleep",
        "unit": None, "icon": "mdi:sleep",
        "category": "readiness_components", "field": "sleep_component",
    },
    "hrv_component": {
        "name": "Readiness - HRV",
        "unit": None, "icon": "mdi:heart-flash",
        "category": "readiness_components", "field": "hrv_component",
    },
    "rhr_component": {
        "name": "Readiness - RHR",
        "unit": None, "icon": "mdi:heart",
        "category": "readiness_components", "field": "rhr_component",
    },
    "recovery_component": {
        "name": "Readiness - Recovery",
        "unit": None, "icon": "mdi:refresh",
        "category": "readiness_components", "field": "recovery_component",
    },
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zepp Health from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    # Forward to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register the import_history service (re-import from stored file)
    async def handle_import_history(_call: ServiceCall) -> None:
        """Handle the import_history service call."""
        await _import_all_statistics(hass, entry.data["file_path"])

    hass.services.async_register(DOMAIN, "import_history", handle_import_history)

    # Register the import_file service (upload a new JSON file from HA UI)
    async def handle_import_file(call: ServiceCall) -> None:
        """Handle the import_file service call - accepts an uploaded file."""
        file_id = call.data.get("file")
        if not file_id:
            _LOGGER.error("No file provided to import_file service")
            return

        upload = await FileUploadData.async_get_instance(hass)
        file_path = upload.get_file_path(file_id)
        contents = await hass.async_add_executor_job(_read_file, str(file_path))
        await hass.async_add_executor_job(upload.remove_file, file_id)

        # Validate
        try:
            data = json.loads(contents)
            if "data" not in data:
                _LOGGER.error("Invalid Zepp Health export format")
                return
        except json.JSONDecodeError as err:
            _LOGGER.error("Invalid JSON in uploaded file: %s", err)
            return

        # Save to storage
        storage_path = hass.config.path(STORAGE_DIR)
        os.makedirs(storage_path, mode=0o755, exist_ok=True)
        dest = os.path.join(storage_path, EXPORT_FILENAME)
        await hass.async_add_executor_job(_write_file, dest, contents)

        # Update entry data to point to new file
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "file_path": dest}
        )

        _LOGGER.info("New export file saved, starting import...")
        await _import_all_statistics(hass, dest)

        # Reload sensors to pick up new latest values
        await hass.config_entries.async_reload(entry.entry_id)

    hass.services.async_register(
        DOMAIN,
        "import_file",
        handle_import_file,
        schema=vol.Schema(
            {vol.Required("file"): FileSelector(FileSelectorConfig(accept=".json"))}
        ),
    )

    # Auto-import on first setup
    hass.async_create_task(_import_all_statistics(hass, entry.data["file_path"]))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _import_all_statistics(hass: HomeAssistant, file_path: str) -> None:
    """Import all historical data as long-term statistics."""
    _LOGGER.info("Starting Zepp Health historical data import from %s", file_path)

    try:
        data = await hass.async_add_executor_job(_load_export_file, file_path)
    except (FileNotFoundError, json.JSONDecodeError) as err:
        _LOGGER.error("Failed to load export file: %s", err)
        return

    imported_count = 0

    for sensor_key, sensor_def in SENSOR_DEFINITIONS.items():
        # Skip "resting_hr" alias (it's the same data as sleep_hr)
        if sensor_key == "resting_hr":
            continue

        count = _import_sensor_statistics(hass, data, sensor_key, sensor_def)
        imported_count += count

    _LOGGER.info(
        "Zepp Health import complete: %d total data points imported", imported_count
    )


def _import_sensor_statistics(
    hass: HomeAssistant, data: dict, sensor_key: str, sensor_def: dict
) -> int:
    """Import statistics for a single sensor and return data point count."""
    category = sensor_def["category"]
    field = sensor_def["field"]
    dataset = data.get("data", {}).get(category, [])
    if not dataset:
        return 0

    statistics = []
    for entry in dataset:
        value = entry.get(field)
        if value is None:
            continue

        # Parse the date to get a timestamp
        date_str = entry.get("date")
        if not date_str:
            continue

        try:
            # Each data point represents a daily value at noon UTC
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
                hour=12, tzinfo=timezone.utc
            )
        except ValueError:
            continue

        try:
            value = float(value)
        except (TypeError, ValueError):
            continue

        statistics.append(
            StatisticData(
                start=dt,
                state=value,
                mean=value,
                min=value,
                max=value,
                sum=None,
            )
        )

    if not statistics:
        return 0

    # Sort by time
    statistics.sort(key=lambda s: s["start"])

    # Define the statistic metadata
    statistic_id = f"{DOMAIN}:{sensor_key}"
    metadata = StatisticMetaData(
        has_mean=True,
        has_sum=False,
        name=sensor_def["name"],
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement=sensor_def["unit"],
    )

    # Import into recorder
    async_import_statistics(hass, metadata, statistics)

    _LOGGER.debug(
        "Imported %d data points for %s", len(statistics), sensor_key
    )
    return len(statistics)


def _load_export_file(file_path: str) -> dict:
    """Load and parse the JSON export file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_file(path: str) -> str:
    """Read a file as string."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_file(path: str, content: str) -> None:
    """Write a string to a file."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
