"""Shared fixtures for the Valetudo Camera tests."""

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.valetudo_camera.api import (
    DuststreamingState,
    ValetudoClient,
    ValetudoRobotInfo,
)

SYSTEM_ID = "ExampleSystemId"


@pytest.fixture
def hass_config_dir(tmp_path: Path) -> str:
    """Load this integration into an isolated Home Assistant config directory."""
    (tmp_path / "custom_components").symlink_to(
        Path(__file__).resolve().parents[1] / "custom_components",
        target_is_directory=True,
    )
    return str(tmp_path)


@pytest.fixture
def robot_info() -> ValetudoRobotInfo:
    """Describe a robot that is not known to the MQTT integration."""
    return ValetudoRobotInfo(SYSTEM_ID, "Acme", "Robot 1", "2026.08.0", None)


@pytest.fixture
def api(
    enable_custom_integrations: None, robot_info: ValetudoRobotInfo
) -> Generator[SimpleNamespace]:
    """Replace the robot's HTTP API.

    Yields the mocked calls as ``info`` and ``state`` so tests can change
    their answers or make them fail.
    """
    state = DuststreamingState(enabled=True, installed=True, width=640, height=480)
    with (
        patch.object(
            ValetudoClient, "async_get_robot_info", AsyncMock(return_value=robot_info)
        ) as info_call,
        patch.object(
            ValetudoClient,
            "async_get_duststreaming_state",
            AsyncMock(return_value=state),
        ) as state_call,
    ):
        yield SimpleNamespace(info=info_call, state=state_call)
