"""Valetudo duststreaming camera entity."""

from __future__ import annotations

import asyncio
import time

from aiohttp import web
from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ValetudoConfigEntry
from .const import (
    CONF_MJPEG_FPS,
    CONF_MJPEG_QUALITY,
    CONF_SNAPSHOT_CACHE,
    DEFAULT_MJPEG_FPS,
    DEFAULT_MJPEG_QUALITY,
    DEFAULT_SNAPSHOT_CACHE,
    DOMAIN,
)
from .stream import ValetudoStream


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ValetudoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the camera entity."""
    async_add_entities([ValetudoCamera(hass, entry)])


class ValetudoCamera(CoordinatorEntity, Camera):
    """A camera entity exposing Valetudo duststreaming."""

    _attr_has_entity_name = True
    _attr_translation_key = "camera"

    def __init__(self, hass: HomeAssistant, entry: ValetudoConfigEntry) -> None:
        """Initialize the camera entity."""
        super().__init__(entry.runtime_data.coordinator)
        Camera.__init__(self)
        self._entry = entry
        self._stream = ValetudoStream(hass, entry.runtime_data.client)
        self._snapshot: bytes | None = None
        self._snapshot_at = 0.0
        self._snapshot_lock = asyncio.Lock()
        self._attr_unique_id = f"{entry.runtime_data.info.system_id}_camera"
        # A device belongs to one config entry, so reporting the MQTT device's
        # identifiers would create a second, nameless device. Pointing the
        # entity at the existing device shows the camera next to the robot's
        # MQTT entities without touching that device.
        if mqtt_device := self._mqtt_device(hass):
            self.device_entry = mqtt_device
        else:
            self._attr_device_info = self._own_device_info()

    def _mqtt_device(self, hass: HomeAssistant) -> dr.DeviceEntry | None:
        """Return the robot's device from Valetudo's MQTT discovery, if any."""
        mqtt_identifier = self._entry.runtime_data.info.mqtt_identifier
        if not mqtt_identifier:
            return None
        devices = dr.async_get(hass).async_get_devices(
            identifiers={("mqtt", mqtt_identifier)}
        )
        return devices[0] if devices else None

    def _own_device_info(self) -> DeviceInfo:
        """Describe a device for robots that are not set up through MQTT."""
        info = self._entry.runtime_data.info
        return DeviceInfo(
            identifiers={(DOMAIN, info.system_id)},
            manufacturer=info.manufacturer,
            model=info.model,
            name=f"{info.manufacturer} {info.model}",
            sw_version=info.version,
        )

    @property
    @callback
    def available(self) -> bool:
        """Return whether the robot and its stream are ready."""
        state = self.coordinator.data
        return super().available and state.enabled and state.installed

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Expose the stream resolution."""
        state = self.coordinator.data
        return {"width": state.width, "height": state.height}

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a cached frame to avoid repeatedly starting the streamer."""
        cache_seconds = int(
            self._entry.options.get(CONF_SNAPSHOT_CACHE, DEFAULT_SNAPSHOT_CACHE)
        )
        if (
            self._snapshot is not None
            and time.monotonic() - self._snapshot_at < cache_seconds
        ):
            return self._snapshot

        async with self._snapshot_lock:
            if (
                self._snapshot is not None
                and time.monotonic() - self._snapshot_at < cache_seconds
            ):
                return self._snapshot
            self._snapshot = await self._stream.async_snapshot()
            self._snapshot_at = time.monotonic()
            return self._snapshot

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse:
        """Serve the transcoded live stream."""
        return await self._stream.async_mjpeg_response(
            request,
            fps=int(self._entry.options.get(CONF_MJPEG_FPS, DEFAULT_MJPEG_FPS)),
            quality=int(
                self._entry.options.get(CONF_MJPEG_QUALITY, DEFAULT_MJPEG_QUALITY)
            ),
        )
