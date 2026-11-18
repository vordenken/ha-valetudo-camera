"""Coordinator for periodically refreshing Valetudo duststreaming state."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    DuststreamingState,
    ValetudoAuthError,
    ValetudoClient,
    ValetudoError,
)
from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class ValetudoCoordinator(DataUpdateCoordinator[DuststreamingState]):
    """Fetch duststreaming status from one robot."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: ValetudoClient,
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> DuststreamingState:
        """Fetch current status."""
        try:
            return await self.client.async_get_duststreaming_state()
        except ValetudoAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ValetudoError as err:
            raise UpdateFailed(str(err)) from err
