"""Tests for the Valetudo MPEG-TS transcoding pipeline."""

import asyncio
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aiohttp import ClientResponse, ClientSession, web
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.valetudo_camera.api import ValetudoClient
from custom_components.valetudo_camera.const import MJPEG_CONTENT_TYPE
from custom_components.valetudo_camera.stream import ValetudoStream

pytest_plugins = ("aiohttp.pytest_plugin",)


class RecordingValetudoClient(ValetudoClient):
    """Keep the upstream response for cleanup assertions."""

    response: ClientResponse | None = None

    async def async_open_stream(self) -> ClientResponse:
        self.response = await super().async_open_stream()
        return self.response


@pytest.fixture(scope="session")
def mpeg_ts() -> bytes:
    """Generate a small MPEG-1 transport stream using the installed ffmpeg."""
    binary = shutil.which("ffmpeg")
    if binary is None:
        pytest.skip("ffmpeg is required for the stream pipeline tests")
    return subprocess.run(
        [
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x48:rate=25",
            "-frames:v",
            "250",
            "-c:v",
            "mpeg1video",
            "-f",
            "mpegts",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout


@pytest.fixture
def ffmpeg_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the installed ffmpeg instead of requiring a running HA instance."""
    monkeypatch.setattr(
        "custom_components.valetudo_camera.stream.get_ffmpeg_manager",
        lambda hass: SimpleNamespace(binary=shutil.which("ffmpeg")),
    )


async def test_snapshot_does_not_wait_for_upstream_close(
    aiohttp_server,
    socket_enabled,
    mpeg_ts: bytes,
    ffmpeg_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame should be returned even while the robot keeps streaming."""
    release_upstream = asyncio.Event()

    async def stream(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(mpeg_ts)
        await release_upstream.wait()
        return response

    app = web.Application()
    app.router.add_get(
        "/api/v2/robot/capabilities/DuststreamingCapability/stream", stream
    )
    server = await aiohttp_server(app)
    monkeypatch.setattr("custom_components.valetudo_camera.stream.SNAPSHOT_TIMEOUT", 3)
    try:
        async with ClientSession() as session:
            client = RecordingValetudoClient(session, str(server.make_url("/")))
            image = await ValetudoStream(
                Mock(spec=HomeAssistant), client
            ).async_snapshot()
            assert image.startswith(b"\xff\xd8")
            assert image.endswith(b"\xff\xd9")
            assert client.response is not None and client.response.closed
    finally:
        release_upstream.set()
        await server.close()


async def test_snapshot_timeout_releases_upstream(
    aiohttp_server,
    socket_enabled,
    mpeg_ts: bytes,
    ffmpeg_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stalled stream reports the timeout rather than a cleanup error."""
    release_upstream = asyncio.Event()

    async def stream(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse()
        await response.prepare(request)
        await release_upstream.wait()
        return response

    app = web.Application()
    app.router.add_get(
        "/api/v2/robot/capabilities/DuststreamingCapability/stream", stream
    )
    server = await aiohttp_server(app)
    monkeypatch.setattr(
        "custom_components.valetudo_camera.stream.SNAPSHOT_TIMEOUT", 0.5
    )
    try:
        async with ClientSession() as session:
            client = RecordingValetudoClient(session, str(server.make_url("/")))
            with pytest.raises(HomeAssistantError, match="Timed out waiting"):
                await ValetudoStream(Mock(spec=HomeAssistant), client).async_snapshot()
            assert client.response is not None and client.response.closed
    finally:
        release_upstream.set()
        await server.close()


async def test_live_stream_returns_multipart_jpegs(
    aiohttp_server, socket_enabled, mpeg_ts: bytes, ffmpeg_manager
) -> None:
    """A viewer receives multipart JPEGs generated from the robot stream."""

    async def upstream(request: web.Request) -> web.Response:
        return web.Response(body=mpeg_ts, content_type="video/MP2T")

    upstream_app = web.Application()
    upstream_app.router.add_get(
        "/api/v2/robot/capabilities/DuststreamingCapability/stream", upstream
    )
    server = await aiohttp_server(upstream_app)

    async with ClientSession() as session:
        client = ValetudoClient(session, str(server.make_url("/")))
        transcoder = ValetudoStream(Mock(spec=HomeAssistant), client)
        viewer_app = web.Application()
        viewer_app.router.add_get("/", transcoder.async_mjpeg_response)
        viewer = await aiohttp_server(viewer_app)
        async with session.get(viewer.make_url("/")) as response:
            body = await response.read()
            assert response.headers["Content-Type"] == MJPEG_CONTENT_TYPE
            assert b"--ffmpeg" in body
            assert b"Content-type: image/jpeg" in body
            assert b"\xff\xd8" in body
            assert b"\xff\xd9" in body
        await viewer.close()
        await server.close()


async def test_live_stream_ends_cleanly_when_robot_drops_connection(
    aiohttp_server, socket_enabled, mpeg_ts: bytes, ffmpeg_manager
) -> None:
    """The robot cutting the stream ends the response instead of failing it."""
    viewer_has_frame = asyncio.Event()

    async def upstream(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(mpeg_ts)
        # Drop the viewer mid-stream: closing without the final chunk makes
        # the client see an interrupted response.
        await viewer_has_frame.wait()
        request.transport.close()
        return response

    upstream_app = web.Application()
    upstream_app.router.add_get(
        "/api/v2/robot/capabilities/DuststreamingCapability/stream", upstream
    )
    server = await aiohttp_server(upstream_app)
    failures: list[Exception] = []

    async with ClientSession() as session:
        client = RecordingValetudoClient(session, str(server.make_url("/")))
        transcoder = ValetudoStream(Mock(spec=HomeAssistant), client)

        async def view(request: web.Request) -> web.StreamResponse:
            try:
                return await transcoder.async_mjpeg_response(request)
            except Exception as err:
                failures.append(err)
                raise

        viewer_app = web.Application()
        viewer_app.router.add_get("/", view)
        viewer = await aiohttp_server(viewer_app)
        async with session.get(viewer.make_url("/")) as response:
            body = b""
            async for chunk in response.content.iter_any():
                body += chunk
                if b"\xff\xd9" in body:
                    viewer_has_frame.set()
        assert b"\xff\xd8" in body
        assert failures == []
        assert client.response is not None and client.response.closed
        await viewer.close()
        await server.close()


async def test_snapshot_reports_dropped_connection(
    aiohttp_server, socket_enabled, ffmpeg_manager
) -> None:
    """The robot cutting the stream before a frame is named as the cause."""

    async def upstream(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse()
        await response.prepare(request)
        request.transport.abort()
        return response

    app = web.Application()
    app.router.add_get(
        "/api/v2/robot/capabilities/DuststreamingCapability/stream", upstream
    )
    server = await aiohttp_server(app)

    async with ClientSession() as session:
        client = RecordingValetudoClient(session, str(server.make_url("/")))
        with pytest.raises(HomeAssistantError, match="closed the stream"):
            await ValetudoStream(Mock(spec=HomeAssistant), client).async_snapshot()
        assert client.response is not None and client.response.closed
    await server.close()


async def test_live_stream_timeout_releases_upstream(
    aiohttp_server,
    socket_enabled,
    mpeg_ts: bytes,
    ffmpeg_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stalled live stream reports its startup timeout."""
    release_upstream = asyncio.Event()

    async def stream(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse()
        await response.prepare(request)
        await release_upstream.wait()
        return response

    app = web.Application()
    app.router.add_get(
        "/api/v2/robot/capabilities/DuststreamingCapability/stream", stream
    )
    server = await aiohttp_server(app)
    monkeypatch.setattr(
        "custom_components.valetudo_camera.stream.STREAM_START_TIMEOUT", 0.5
    )
    try:
        async with ClientSession() as session:
            client = RecordingValetudoClient(session, str(server.make_url("/")))
            transcoder = ValetudoStream(Mock(spec=HomeAssistant), client)
            with pytest.raises(HomeAssistantError, match="Timed out waiting"):
                await transcoder.async_mjpeg_response(Mock(spec=web.Request))
            assert client.response is not None and client.response.closed
    finally:
        release_upstream.set()
        await server.close()
