"""Tests for setting up and unloading a configured robot."""

import json
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.valetudo_camera.api import (
    ValetudoAuthError,
    ValetudoConnectionError,
    ValetudoNotSupportedError,
)
from custom_components.valetudo_camera.const import DOMAIN
from custom_components.valetudo_camera.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import SYSTEM_ID


def _entry(hass: HomeAssistant, url: str = "https://robot.example") -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Robot",
        unique_id=SYSTEM_ID,
        data={"url": url, "username": "user", "password": "s3cret"},
    )
    entry.add_to_hass(hass)
    return entry


def _reauth_flows(hass: HomeAssistant) -> list[dict]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_unload_removes_camera(hass: HomeAssistant, api: SimpleNamespace) -> None:
    """Unloading the entry takes the camera out of service."""
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    (entity_id,) = hass.states.async_entity_ids("camera")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(entity_id).state == "unavailable"


async def test_rejected_credentials_ask_for_new_ones(
    hass: HomeAssistant, api: SimpleNamespace
) -> None:
    """Wrong credentials at startup start the reauthentication dialog."""
    api.info.side_effect = ValetudoAuthError
    entry = _entry(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(_reauth_flows(hass)) == 1


@pytest.mark.parametrize(
    "error", [ValetudoConnectionError("offline"), ValetudoNotSupportedError()]
)
async def test_unreachable_robot_is_retried(
    hass: HomeAssistant, api: SimpleNamespace, error: Exception
) -> None:
    """A robot that does not answer properly is retried instead of given up."""
    api.info.side_effect = error
    entry = _entry(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not _reauth_flows(hass)


async def test_credentials_rejected_later_ask_for_new_ones(
    hass: HomeAssistant, api: SimpleNamespace
) -> None:
    """Credentials that stop working while running also start reauthentication."""
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    api.state.side_effect = ValetudoAuthError
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(_reauth_flows(hass)) == 1


async def test_robot_going_offline_makes_camera_unavailable(
    hass: HomeAssistant, api: SimpleNamespace
) -> None:
    """A robot that stops answering takes the camera offline without reauth."""
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    (entity_id,) = hass.states.async_entity_ids("camera")

    api.state.side_effect = ValetudoConnectionError("offline")
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "unavailable"
    assert not _reauth_flows(hass)


async def test_plain_http_with_credentials_is_logged(
    hass: HomeAssistant, api: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    """Sending credentials over plain HTTP is allowed but pointed out."""
    entry = _entry(hass, url="http://192.0.2.10")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert "credentials are sent unencrypted" in caplog.text


async def test_diagnostics_contain_no_credentials(
    hass: HomeAssistant, api: SimpleNamespace
) -> None:
    """Diagnostics describe the robot without leaking the connection data."""
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["robot"]["system_id"] == SYSTEM_ID
    assert diagnostics["duststreaming"]["enabled"] is True
    dumped = json.dumps(diagnostics)
    assert "s3cret" not in dumped
    assert "robot.example" not in dumped
