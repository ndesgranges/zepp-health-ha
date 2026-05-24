"""Config flow for Zepp Health Import."""

from __future__ import annotations

import json
import logging
import os

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.helpers.selector import FileSelector, FileSelectorConfig

from .const import DOMAIN, STORAGE_DIR, EXPORT_FILENAME

_LOGGER = logging.getLogger(__name__)


class ZeppHealthConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zepp Health Import."""

    VERSION = 1

    async def async_step_user(self, _user_input=None):
        """Handle the initial step - choose input method."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["upload", "file_path"],
        )

    async def async_step_upload(self, user_input=None):
        """Handle file upload step."""
        errors = {}

        if user_input is not None:
            uploaded_file_id = user_input.get("file")
            if uploaded_file_id:
                try:
                    contents = await self._read_uploaded_file(uploaded_file_id)
                    data = json.loads(contents)

                    if "data" not in data:
                        errors["file"] = "invalid_format"
                    else:
                        dest = await self._save_export(contents)
                        await self.async_set_unique_id("zepp_health_import")
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title="Zepp Health",
                            data={"file_path": dest, "source": "upload"},
                        )
                except json.JSONDecodeError:
                    errors["file"] = "invalid_json"
                except (OSError, KeyError) as err:
                    _LOGGER.error("Upload error: %s", err)
                    errors["file"] = "unknown"

        return self.async_show_form(
            step_id="upload",
            data_schema=vol.Schema(
                {
                    vol.Required("file"): FileSelector(
                        FileSelectorConfig(accept=".json")
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_file_path(self, user_input=None):
        """Handle manual file path step (for advanced users)."""
        errors = {}

        if user_input is not None:
            file_path = user_input["file_path"]
            if not os.path.isfile(file_path):
                errors["file_path"] = "file_not_found"
            else:
                try:
                    content = await self.hass.async_add_executor_job(
                        _read_file, file_path
                    )
                    data = json.loads(content)
                    if "data" not in data:
                        errors["file_path"] = "invalid_format"
                except (json.JSONDecodeError, OSError):
                    errors["file_path"] = "invalid_json"

                if not errors:
                    await self.async_set_unique_id("zepp_health_import")
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title="Zepp Health",
                        data={"file_path": file_path, "source": "path"},
                    )

        return self.async_show_form(
            step_id="file_path",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "file_path",
                        default="/config/zepp_health_export.json",
                    ): str,
                }
            ),
            errors=errors,
        )

    async def _read_uploaded_file(self, file_id: str) -> str:
        """Read an uploaded file from HA's file upload store."""
        with process_uploaded_file(self.hass, file_id) as file_path:
            content = await self.hass.async_add_executor_job(
                _read_file, str(file_path)
            )
        return content

    async def _save_export(self, contents: str) -> str:
        """Save export file to HA's storage area."""
        storage_path = self.hass.config.path(STORAGE_DIR)
        await self.hass.async_add_executor_job(os.makedirs, storage_path, 0o755, True)
        dest = os.path.join(storage_path, EXPORT_FILENAME)
        await self.hass.async_add_executor_job(_write_file, dest, contents)
        return dest


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
