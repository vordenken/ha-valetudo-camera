"""Minimal client for the Valetudo HTTP API (duststreaming related parts only).

This client deliberately never touches the write endpoints of the duststreaming
feature: disabling it via PUT or calling the camera killswitch deletes the
duststreamer binary from the robot.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import aiohttp

CAPABILITY = "DuststreamingCapability"
STREAM_PATH = f"/api/v2/robot/capabilities/{CAPABILITY}/stream"


class ValetudoError(Exception):
    """Base error."""


class ValetudoConnectionError(ValetudoError):
    """Robot not reachable or returned an unexpected response."""


class ValetudoCertificateError(ValetudoConnectionError):
    """The TLS certificate of the robot or its reverse proxy is not trusted."""


class ValetudoAuthError(ValetudoError):
    """Invalid credentials."""


class ValetudoNotSupportedError(ValetudoError):
    """Robot or Valetudo version has no duststreaming capability."""


class ValetudoStreamingDisabledError(ValetudoError):
    """Duststreaming is disabled in the Valetudo UI."""


class ValetudoStreamUnavailableError(ValetudoError):
    """Duststreamer is missing or currently unable to accept subscribers."""


@dataclass(frozen=True, slots=True)
class ValetudoRobotInfo:
    """Static robot information."""

    system_id: str
    manufacturer: str
    model: str
    version: str | None
    mqtt_identifier: str | None


@dataclass(frozen=True, slots=True)
class DuststreamingState:
    """Polled duststreaming state."""

    enabled: bool
    installed: bool
    width: int
    height: int


class ValetudoClient:
    """Talks to a single Valetudo instance."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._session = session
        self.url = url.rstrip("/")
        self._headers = (
            {"Authorization": aiohttp.encode_basic_auth(username, password or "")}
            if username
            else None
        )

    async def _get_json(self, path: str) -> Any:
        try:
            async with asyncio.timeout(10):
                async with self._session.get(
                    f"{self.url}{path}", headers=self._headers
                ) as resp:
                    if resp.status == 401:
                        raise ValetudoAuthError
                    if resp.status == 404:
                        raise ValetudoNotSupportedError(path)
                    if resp.status != 200:
                        raise ValetudoConnectionError(
                            f"{path} returned HTTP {resp.status}"
                        )
                    return await resp.json(content_type=None)
        except aiohttp.ClientConnectorCertificateError as err:
            raise ValetudoCertificateError(str(err)) from err
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise ValetudoConnectionError(str(err) or type(err).__name__) from err

    async def async_get_robot_info(self) -> ValetudoRobotInfo:
        """Fetch identifying information and verify duststreaming support."""
        valetudo, robot, capabilities = await asyncio.gather(
            self._get_json("/api/v2/valetudo"),
            self._get_json("/api/v2/robot"),
            self._get_json("/api/v2/robot/capabilities"),
        )
        # Anything else answering on this URL (e.g. a proxy page that returns
        # JSON) must not be set up as a robot.
        if (
            not isinstance(valetudo, dict)
            or not isinstance(robot, dict)
            or not isinstance(capabilities, list)
            or not valetudo.get("systemId")
        ):
            raise ValetudoConnectionError("The URL did not answer like Valetudo")
        if CAPABILITY not in capabilities:
            raise ValetudoNotSupportedError
        system_id: str = valetudo["systemId"]

        version: str | None = None
        mqtt_identifier: str | None = None
        try:
            version = (await self._get_json("/api/v2/valetudo/version")).get("release")
            mqtt = await self._get_json("/api/v2/valetudo/config/interfaces/mqtt")
            if mqtt.get("enabled"):
                mqtt_identifier = (mqtt.get("identity") or {}).get("identifier")
        except ValetudoNotSupportedError:
            pass

        return ValetudoRobotInfo(
            system_id=system_id,
            manufacturer=robot.get("manufacturer", "Valetudo"),
            model=robot.get("modelName", "Robot"),
            version=version,
            mqtt_identifier=mqtt_identifier or None,
        )

    async def async_get_duststreaming_state(self) -> DuststreamingState:
        """Fetch the current duststreaming state."""
        config, props = await asyncio.gather(
            self._get_json("/api/v2/valetudo/config/duststreaming"),
            self._get_json(f"/api/v2/robot/capabilities/{CAPABILITY}/properties"),
        )
        return DuststreamingState(
            enabled=config.get("enabled") is True,
            installed=props.get("duststreamerInstalled") is True,
            width=int(props.get("width", 0)),
            height=int(props.get("height", 0)),
        )

    async def async_open_stream(self) -> aiohttp.ClientResponse:
        """Open the raw MPEG-TS stream. The caller must release the response."""
        try:
            resp = await self._session.get(
                f"{self.url}{STREAM_PATH}",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=30),
            )
        except aiohttp.ClientConnectorCertificateError as err:
            raise ValetudoCertificateError(str(err)) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ValetudoConnectionError(str(err) or type(err).__name__) from err

        if resp.status == 200:
            return resp
        resp.release()
        if resp.status == 401:
            raise ValetudoAuthError
        if resp.status == 403:
            raise ValetudoStreamingDisabledError
        if resp.status == 404:
            raise ValetudoNotSupportedError
        if resp.status == 503:
            raise ValetudoStreamUnavailableError
        raise ValetudoConnectionError(f"Stream returned HTTP {resp.status}")
