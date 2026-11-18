"""Tests for the Valetudo camera entity."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.camera import async_get_image, async_get_mjpeg_stream
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.valetudo_camera.api import (
    DuststreamingState,
    ValetudoRobotInfo,
)
from custom_components.valetudo_camera.const import DOMAIN
from custom_components.valetudo_camera.stream import ValetudoStream

from .conftest import SYSTEM_ID


async def _setup(hass: HomeAssistant, **options: int) -> str:
    """Set up one robot and return its camera entity ID."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Robot",
        unique_id=SYSTEM_ID,
        data={"url": "http://127.0.0.1"},
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    (entity_id,) = hass.states.async_entity_ids("camera")
    return entity_id


async def test_camera_registration(hass: HomeAssistant, api: SimpleNamespace) -> None:
    """Home Assistant registers a camera with a usable image URL."""
    entity_id = await _setup(hass)

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state != "unavailable"
    assert (state.attributes["width"], state.attributes["height"]) == (640, 480)
    token = state.attributes["access_token"]
    assert token
    assert state.attributes["entity_picture"].endswith(f"?token={token}")


@pytest.mark.parametrize(
    "duststreaming",
    [
        DuststreamingState(enabled=False, installed=True, width=640, height=480),
        DuststreamingState(enabled=True, installed=False, width=640, height=480),
    ],
    ids=["disabled in Valetudo", "streamer not installed"],
)
async def test_camera_unavailable_without_duststreaming(
    hass: HomeAssistant, api: SimpleNamespace, duststreaming: DuststreamingState
) -> None:
    """The camera is unavailable while the robot cannot stream."""
    api.state.return_value = duststreaming

    entity_id = await _setup(hass)

    assert hass.states.get(entity_id).state == "unavailable"


async def test_snapshot_is_cached(hass: HomeAssistant, api: SimpleNamespace) -> None:
    """Repeated image requests reuse one frame instead of reopening the stream."""
    entity_id = await _setup(hass)

    with patch.object(
        ValetudoStream, "async_snapshot", AsyncMock(return_value=b"jpeg")
    ) as snapshot:
        first = await async_get_image(hass, entity_id)
        second = await async_get_image(hass, entity_id)

    assert first.content == second.content == b"jpeg"
    snapshot.assert_awaited_once()


async def test_snapshot_cache_can_be_disabled(
    hass: HomeAssistant, api: SimpleNamespace
) -> None:
    """A cache time of zero fetches a fresh frame for every request."""
    entity_id = await _setup(hass, snapshot_cache_seconds=0)

    with patch.object(
        ValetudoStream, "async_snapshot", AsyncMock(return_value=b"jpeg")
    ) as snapshot:
        await async_get_image(hass, entity_id)
        await async_get_image(hass, entity_id)

    assert snapshot.await_count == 2


async def test_live_stream_uses_configured_options(
    hass: HomeAssistant, api: SimpleNamespace
) -> None:
    """Frame rate and quality from the options reach the transcoder."""
    entity_id = await _setup(hass, mjpeg_fps=5, mjpeg_quality=10)
    request = Mock()

    with patch.object(
        ValetudoStream, "async_mjpeg_response", AsyncMock(return_value=Mock())
    ) as live:
        await async_get_mjpeg_stream(hass, request, entity_id)

    live.assert_awaited_once_with(request, fps=5, quality=10)


async def test_camera_joins_existing_mqtt_device(
    hass: HomeAssistant, api: SimpleNamespace, robot_info: ValetudoRobotInfo
) -> None:
    """The camera appears on the robot's MQTT device without changing it."""
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    mqtt_device = devices.async_get_or_create(
        config_entry_id=mqtt_entry.entry_id,
        identifiers={("mqtt", "robot1")},
        manufacturer="Valetudo",
        name="Hallway",
    )
    api.info.return_value = ValetudoRobotInfo(
        SYSTEM_ID, "Acme", "Robot 1", "2026.08.0", "robot1"
    )

    entity_id = await _setup(hass)

    assert er.async_get(hass).async_get(entity_id).device_id == mqtt_device.id
    device = devices.async_get(mqtt_device.id)
    assert (device.name, device.manufacturer) == ("Hallway", "Valetudo")
    assert len(devices.devices) == 1


async def test_camera_creates_own_device_without_mqtt(
    hass: HomeAssistant, api: SimpleNamespace
) -> None:
    """Without a matching MQTT device the camera gets a device of its own."""
    entity_id = await _setup(hass)

    device_id = er.async_get(hass).async_get(entity_id).device_id
    device = dr.async_get(hass).async_get(device_id)
    assert device.identifiers == {(DOMAIN, SYSTEM_ID)}
    assert (device.manufacturer, device.model) == ("Acme", "Robot 1")
