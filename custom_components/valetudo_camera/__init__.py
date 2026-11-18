"""Valetudo duststreaming camera integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ValetudoAuthError,
    ValetudoClient,
    ValetudoConnectionError,
    ValetudoError,
    ValetudoRobotInfo,
)
from .const import CONF_URL, CONF_VERIFY_SSL
from .coordinator import ValetudoCoordinator

PLATFORMS = ["camera"]

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ValetudoRuntimeData:
    """Data shared by entities for a configured robot."""

    client: ValetudoClient
    info: ValetudoRobotInfo
    coordinator: ValetudoCoordinator


type ValetudoConfigEntry = ConfigEntry[ValetudoRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: ValetudoConfigEntry) -> bool:
    """Set up a configured Valetudo robot."""
    if entry.data.get("username") and entry.data[CONF_URL].startswith("http://"):
        _LOGGER.warning(
            "%s uses basic auth over plain HTTP; credentials are sent unencrypted",
            entry.title,
        )
    session = async_get_clientsession(
        hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)
    )
    client = ValetudoClient(
        session,
        entry.data[CONF_URL],
        entry.data.get("username"),
        entry.data.get("password"),
    )
    try:
        info = await client.async_get_robot_info()
    except ValetudoAuthError:
        raise ConfigEntryAuthFailed("Authentication required") from None
    except ValetudoConnectionError as err:
        raise ConfigEntryNotReady from err
    except ValetudoError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = ValetudoCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = ValetudoRuntimeData(client, info, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ValetudoConfigEntry) -> bool:
    """Unload a configured Valetudo robot."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
