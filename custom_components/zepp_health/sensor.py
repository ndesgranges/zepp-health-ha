"""Sensor platform for Zepp Health Import.

Creates sensor entities that reflect the latest values from the export
and link to the long-term statistics for history display.
"""

from __future__ import annotations

import json
import logging

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SENSOR_DEFINITIONS
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zepp Health sensors from a config entry."""
    file_path = entry.data["file_path"]

    try:
        data = await hass.async_add_executor_job(_load_file, file_path)
    except (FileNotFoundError, json.JSONDecodeError) as err:
        _LOGGER.error("Failed to load Zepp Health export: %s", err)
        return

    entities = []

    for sensor_key, sensor_def in SENSOR_DEFINITIONS.items():
        # Skip the "resting_hr" alias
        if sensor_key == "resting_hr":
            continue

        value, last_date = _find_latest_value(data, sensor_def)

        entities.append(
            ZeppHealthSensor(
                sensor_key=sensor_key,
                sensor_def=sensor_def,
                value=value,
                last_date=last_date,
                file_path=file_path,
            )
        )

    async_add_entities(entities, True)


def _find_latest_value(data: dict, sensor_def: dict) -> tuple:
    """Find the most recent value for a sensor from the export data."""
    category = sensor_def["category"]
    field = sensor_def["field"]
    dataset = data.get("data", {}).get(category, [])

    for entry_data in reversed(dataset):
        val = entry_data.get(field)
        if val is not None:
            return val, entry_data.get("date")
    return None, None


class ZeppHealthSensor(SensorEntity):  # pylint: disable=too-many-instance-attributes
    """A sensor representing a Zepp health metric with full history."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        sensor_key: str,
        sensor_def: dict,
        value,
        last_date: str | None,
        file_path: str,
    ) -> None:
        """Initialize the sensor."""
        self._sensor_key = sensor_key
        self._sensor_def = sensor_def
        self._file_path = file_path
        self._attr_unique_id = f"zepp_health_{sensor_key}"
        self._attr_name = sensor_def["name"]
        self._attr_native_unit_of_measurement = sensor_def["unit"]
        self._attr_icon = sensor_def["icon"]
        self._attr_native_value = value
        self._last_date = last_date

    @property
    def device_info(self):
        """Return device info to group all sensors."""
        return {
            "identifiers": {(DOMAIN, "zepp_watch")},
            "name": "Zepp Watch",
            "manufacturer": "Zepp / Amazfit",
            "model": "Health Tracker",
        }

    @property
    def extra_state_attributes(self):
        """Return additional attributes."""
        attrs = {}
        if self._last_date:
            attrs["last_data_date"] = self._last_date
        attrs["data_source"] = "zepp_health_export"
        return attrs

    async def async_update(self) -> None:
        """Update sensor by re-reading the export file."""
        try:
            data = await self.hass.async_add_executor_job(
                _load_file, self._file_path
            )
        except (FileNotFoundError, json.JSONDecodeError):
            return

        category = self._sensor_def["category"]
        field = self._sensor_def["field"]
        dataset = data.get("data", {}).get(category, [])

        for entry_data in reversed(dataset):
            v = entry_data.get(field)
            if v is not None:
                self._attr_native_value = v
                self._last_date = entry_data.get("date")
                break


def _load_file(file_path: str) -> dict:
    """Load the export JSON file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)
