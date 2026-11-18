"""Tests for the Valetudo Camera config flow."""

from collections.abc import Generator
from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.valetudo_camera.api import (
    DuststreamingState,
    ValetudoAuthError,
    ValetudoCertificateError,
    ValetudoConnectionError,
    ValetudoNotSupportedError,
    ValetudoRobotInfo,
)
from custom_components.valetudo_camera.const import DOMAIN

from .conftest import SYSTEM_ID

INFO = ValetudoRobotInfo(SYSTEM_ID, "Acme", "Robot 1", "2026.08.0", None)
DISCOVERY = ZeroconfServiceInfo(
    ip_address=ip_address("192.0.2.10"),
    ip_addresses=[ip_address("192.0.2.10")],
    port=80,
    hostname="valetudo-examplesystemid.local.",
    type="_valetudo._tcp.local.",
    name="Valetudo ExampleSystemId._valetudo._tcp.local.",
    properties={
        "id": SYSTEM_ID,
        "name": "Hallway",
        "manufacturer": "Acme",
        "model": "Robot 1",
    },
)


@pytest.fixture(autouse=True)
def robot(api: SimpleNamespace) -> Generator[AsyncMock]:
    """Answer API calls for one robot without setting up created entries.

    Yields the mocked robot-info call so tests can make it fail.
    """
    with patch(
        "custom_components.valetudo_camera.async_setup_entry",
        AsyncMock(return_value=True),
    ):
        yield api.info


@pytest.fixture
def verify_ssl_calls() -> Generator[list[bool]]:
    """Record whether each connection attempt verified the certificate."""
    calls: list[bool] = []

    def session(hass: HomeAssistant, verify_ssl: bool = True) -> Mock:
        calls.append(verify_ssl)
        return Mock()

    with patch(
        "custom_components.valetudo_camera.config_flow.async_get_clientsession",
        session,
    ):
        yield calls


async def _discover(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )


async def test_discovery_of_configured_robot_is_ignored(hass: HomeAssistant) -> None:
    """A configured robot must not produce another setup dialog."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SYSTEM_ID, data={"url": "https://proxy.example"}
    )
    entry.add_to_hass(hass)

    result = await _discover(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert entry.data["url"] == "https://proxy.example"


async def test_repeated_discovery_keeps_one_dialog(hass: HomeAssistant) -> None:
    """The same robot announcing itself twice yields a single dialog."""
    first = await _discover(hass)
    second = await _discover(hass)

    assert first["type"] is FlowResultType.FORM
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_in_progress"
    assert len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 1


async def test_discovery_card_shows_robot_name(hass: HomeAssistant) -> None:
    """The discovery card names the robot instead of the raw mDNS record."""
    form = await _discover(hass)

    flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    assert flow["context"]["title_placeholders"] == {"name": "Hallway (Acme Robot 1)"}
    defaults = {str(key): key.default() for key in form["data_schema"].schema}
    assert defaults["url"] == "http://192.0.2.10"


async def test_connection_form_has_no_tls_switch(hass: HomeAssistant) -> None:
    """Certificate verification is not offered up front."""
    form = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert {str(key) for key in form["data_schema"].schema} == {
        "url",
        "username",
        "password",
    }


async def test_untrusted_certificate_needs_confirmation(
    hass: HomeAssistant, robot: AsyncMock, verify_ssl_calls: list[bool]
) -> None:
    """Verification is only disabled after the user confirms it."""
    robot.side_effect = [ValetudoCertificateError("self-signed"), INFO]
    form = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    confirm = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"url": "https://robot.example"}
    )

    assert confirm["type"] is FlowResultType.FORM
    assert confirm["step_id"] == "certificate"
    assert confirm["description_placeholders"] == {"url": "https://robot.example"}
    assert verify_ssl_calls == [True]

    result = await hass.config_entries.flow.async_configure(confirm["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["verify_ssl"] is False
    assert verify_ssl_calls == [True, False]


async def test_reconfigure_verifies_certificate_again(
    hass: HomeAssistant, verify_ssl_calls: list[bool]
) -> None:
    """Moving to a trusted URL turns certificate verification back on."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SYSTEM_ID,
        data={"url": "https://self-signed.example", "verify_ssl": False},
    )
    entry.add_to_hass(hass)
    form = await entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"url": "https://trusted.example"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert verify_ssl_calls == [True]
    assert entry.data["url"] == "https://trusted.example"
    assert entry.data["verify_ssl"] is True


async def test_manual_setup_replaces_discovery_dialog(hass: HomeAssistant) -> None:
    """Adding a discovered robot manually works and clears the discovery."""
    await _discover(hass)

    form = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {"url": "https://proxy.example/", "username": "user", "password": "s3cret"},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["url"] == "https://proxy.example"
    assert result["result"].unique_id == SYSTEM_ID
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_password_fields_are_masked(hass: HomeAssistant) -> None:
    """Every form asking for a password renders it as a masked input."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SYSTEM_ID, data={"url": "https://proxy.example"}
    )
    entry.add_to_hass(hass)
    forms = [
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        ),
        await entry.start_reauth_flow(hass),
        await entry.start_reconfigure_flow(hass),
    ]

    for form in forms:
        selectors = {
            str(key): value for key, value in form["data_schema"].schema.items()
        }
        assert selectors["password"].config["type"] == "password", form["step_id"]


async def _user_form(hass: HomeAssistant) -> str:
    form = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return form["flow_id"]


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (ValetudoAuthError(), "invalid_auth"),
        (ValetudoNotSupportedError(), "not_supported"),
        (ValetudoConnectionError("offline"), "cannot_connect"),
    ],
)
async def test_setup_problem_is_explained_and_can_be_corrected(
    hass: HomeAssistant, robot: AsyncMock, failure: Exception, error: str
) -> None:
    """A failed attempt names the problem and the form can be submitted again."""
    flow_id = await _user_form(hass)
    robot.side_effect = failure

    failed = await hass.config_entries.flow.async_configure(
        flow_id, {"url": "https://robot.example"}
    )

    assert failed["type"] is FlowResultType.FORM
    assert failed["errors"] == {"base": error}

    robot.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"url": "https://robot.example"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Acme Robot 1"


async def test_disabled_duststreaming_is_explained(
    hass: HomeAssistant, api: SimpleNamespace
) -> None:
    """A robot with the camera stream switched off is not set up."""
    api.state.return_value = DuststreamingState(
        enabled=False, installed=True, width=640, height=480
    )

    result = await hass.config_entries.flow.async_configure(
        await _user_form(hass), {"url": "https://robot.example"}
    )

    assert result["errors"] == {"base": "streaming_disabled"}


@pytest.mark.parametrize(
    "url",
    ["robot.example", "ftp://robot.example", "https://user:pw@robot.example", ""],
)
async def test_malformed_url_is_rejected_without_connecting(
    hass: HomeAssistant, robot: AsyncMock, url: str
) -> None:
    """URLs that are not a plain HTTP(S) address never reach the network."""
    result = await hass.config_entries.flow.async_configure(
        await _user_form(hass), {"url": url}
    )

    assert result["errors"] == {"base": "cannot_connect"}
    robot.assert_not_awaited()


async def test_robot_cannot_be_added_twice(hass: HomeAssistant) -> None:
    """Adding a configured robot through another URL is refused."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=SYSTEM_ID, data={"url": "https://proxy.example"}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_configure(
        await _user_form(hass), {"url": "http://192.0.2.10"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_certificate_confirmation_reports_other_problems(
    hass: HomeAssistant, robot: AsyncMock
) -> None:
    """A problem that remains after accepting the certificate is still shown."""
    robot.side_effect = [ValetudoCertificateError("self-signed"), ValetudoAuthError()]
    confirm = await hass.config_entries.flow.async_configure(
        await _user_form(hass), {"url": "https://robot.example"}
    )

    result = await hass.config_entries.flow.async_configure(confirm["flow_id"], {})

    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reauth_stores_new_credentials(
    hass: HomeAssistant, robot: AsyncMock
) -> None:
    """Rejected credentials can be replaced; wrong ones are refused."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SYSTEM_ID,
        data={"url": "https://robot.example", "username": "user", "password": "old"},
    )
    entry.add_to_hass(hass)
    form = await entry.start_reauth_flow(hass)
    robot.side_effect = ValetudoAuthError

    failed = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"username": "user", "password": "wrong"}
    )

    assert failed["errors"] == {"base": "invalid_auth"}
    assert entry.data["password"] == "old"

    robot.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"username": "user", "password": "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new"
    assert entry.data["url"] == "https://robot.example"


async def test_reconfigure_keeps_stored_password(hass: HomeAssistant) -> None:
    """The stored password is not shown and survives an empty password field."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SYSTEM_ID,
        data={"url": "http://192.0.2.10", "username": "user", "password": "s3cret"},
    )
    entry.add_to_hass(hass)
    form = await entry.start_reconfigure_flow(hass)
    defaults = {str(key): key.default() for key in form["data_schema"].schema}
    assert defaults == {"url": "http://192.0.2.10", "username": "user", "password": ""}

    await hass.config_entries.flow.async_configure(
        form["flow_id"], {"url": "https://robot.example", "username": "user"}
    )

    assert entry.data["url"] == "https://robot.example"
    assert entry.data["password"] == "s3cret"


async def test_reconfigure_can_remove_credentials(hass: HomeAssistant) -> None:
    """Clearing the username drops the stored password as well."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SYSTEM_ID,
        data={"url": "http://192.0.2.10", "username": "user", "password": "s3cret"},
    )
    entry.add_to_hass(hass)
    form = await entry.start_reconfigure_flow(hass)

    await hass.config_entries.flow.async_configure(
        form["flow_id"], {"url": "http://192.0.2.10", "username": ""}
    )

    assert (entry.data["username"], entry.data["password"]) == ("", "")


async def test_reconfigure_refuses_a_different_robot(
    hass: HomeAssistant, robot: AsyncMock
) -> None:
    """Pointing an entry at another robot would mix up two cameras."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SYSTEM_ID, data={"url": "http://192.0.2.10"}
    )
    entry.add_to_hass(hass)
    form = await entry.start_reconfigure_flow(hass)
    robot.return_value = ValetudoRobotInfo("OtherRobot", "Acme", "Robot 2", None, None)

    result = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"url": "http://192.0.2.11"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data["url"] == "http://192.0.2.10"


async def test_reconfigure_with_untrusted_certificate(
    hass: HomeAssistant, robot: AsyncMock
) -> None:
    """The certificate question also applies when changing the URL."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SYSTEM_ID, data={"url": "http://192.0.2.10"}
    )
    entry.add_to_hass(hass)
    form = await entry.start_reconfigure_flow(hass)
    robot.side_effect = [ValetudoCertificateError("self-signed"), INFO]

    confirm = await hass.config_entries.flow.async_configure(
        form["flow_id"], {"url": "https://robot.example"}
    )
    assert confirm["step_id"] == "certificate"
    result = await hass.config_entries.flow.async_configure(confirm["flow_id"], {})

    assert result["reason"] == "reconfigure_successful"
    assert entry.data["url"] == "https://robot.example"
    assert entry.data["verify_ssl"] is False


async def test_options_are_stored(hass: HomeAssistant) -> None:
    """Snapshot cache, frame rate and quality can be changed after setup."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SYSTEM_ID, data={"url": "http://192.0.2.10"}
    )
    entry.add_to_hass(hass)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    defaults = {str(key): key.default() for key in form["data_schema"].schema}
    assert defaults == {
        "snapshot_cache_seconds": 30,
        "mjpeg_fps": 10,
        "mjpeg_quality": 5,
    }

    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {"snapshot_cache_seconds": 60, "mjpeg_fps": 5, "mjpeg_quality": 10},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "snapshot_cache_seconds": 60,
        "mjpeg_fps": 5,
        "mjpeg_quality": 10,
    }
