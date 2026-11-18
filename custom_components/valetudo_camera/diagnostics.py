"""Diagnostics for Valetudo Camera."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import ValetudoConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ValetudoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without credentials."""
    runtime = entry.runtime_data
    return {
        "robot": asdict(runtime.info),
        "duststreaming": asdict(runtime.coordinator.data),
        "options": dict(entry.options),
    }
