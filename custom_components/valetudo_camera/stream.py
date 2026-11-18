"""MPEG-TS to MJPEG streaming and snapshot helpers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError, ClientResponse, web
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import ValetudoClient, ValetudoError
from .const import (
    DEFAULT_MJPEG_FPS,
    DEFAULT_MJPEG_QUALITY,
    MJPEG_CONTENT_TYPE,
    SNAPSHOT_TIMEOUT,
    STREAM_START_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)
_CHUNK_SIZE = 64 * 1024


def _upstream_drop_reason(task: asyncio.Task[str | None]) -> str | None:
    """Return why the robot ended the stream, if the input pump already knows."""
    if task.done() and not task.cancelled() and task.exception() is None:
        return task.result()
    return None


class ValetudoStream:
    """Transcode one robot's MPEG-TS stream with ffmpeg."""

    def __init__(self, hass: HomeAssistant, client: ValetudoClient) -> None:
        """Initialize the stream helper."""
        self.hass = hass
        self.client = client

    def _ffmpeg_binary(self) -> str:
        """Return Home Assistant's configured ffmpeg executable."""
        return get_ffmpeg_manager(self.hass).binary

    async def _pump_input(
        self, response: ClientResponse, process: asyncio.subprocess.Process
    ) -> str | None:
        """Copy the upstream response into ffmpeg's stdin.

        Returns the reason if the robot ended the stream early. Valetudo does
        that on its own, for example when a fifth viewer pushes out the oldest
        one or when the streamer restarts, so it is not treated as a failure.
        """
        assert process.stdin is not None
        try:
            async for chunk in response.content.iter_chunked(_CHUNK_SIZE):
                process.stdin.write(chunk)
                await process.stdin.drain()
        except BrokenPipeError, ConnectionResetError:
            _LOGGER.debug("ffmpeg stopped reading the Valetudo stream")
        except (ClientError, TimeoutError) as err:
            reason = str(err) or type(err).__name__
            _LOGGER.debug("Valetudo closed the stream: %s", reason)
            return reason
        finally:
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except BrokenPipeError, ConnectionResetError:
                _LOGGER.debug("ffmpeg closed its input before the stream ended")
        return None

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> bytes:
        """Drain ffmpeg stderr to prevent the subprocess from blocking."""
        assert process.stderr is not None
        return await process.stderr.read()

    async def async_snapshot(self) -> bytes:
        """Return a JPEG decoded from the first incoming frame."""
        try:
            upstream = await self.client.async_open_stream()
        except ValetudoError as err:
            raise HomeAssistantError(f"Unable to open Valetudo stream: {err}") from err

        process: asyncio.subprocess.Process | None = None
        input_task: asyncio.Task[str | None] | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                self._ffmpeg_binary(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "mpegts",
                "-i",
                "pipe:0",
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            input_task = asyncio.create_task(self._pump_input(upstream, process))
            stderr_task = asyncio.create_task(self._read_stderr(process))
            assert process.stdout is not None
            async with asyncio.timeout(SNAPSHOT_TIMEOUT):
                image = await process.stdout.read()
                await process.wait()
                stderr = await stderr_task
            if process.returncode != 0 or not image:
                if reason := _upstream_drop_reason(input_task):
                    raise HomeAssistantError(
                        f"Valetudo closed the stream before a frame arrived: {reason}"
                    )
                details = stderr.decode(errors="replace").strip()
                raise HomeAssistantError(
                    "ffmpeg could not decode a Valetudo frame"
                    + (f": {details}" if details else "")
                )
            return image
        except TimeoutError as err:
            raise HomeAssistantError("Timed out waiting for a Valetudo frame") from err
        finally:
            for task in (input_task, stderr_task):
                if task is not None and not task.done():
                    task.cancel()
            upstream.release()
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            for task in (input_task, stderr_task):
                if task is not None:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    async def async_mjpeg_response(
        self,
        request: web.Request,
        *,
        fps: int = DEFAULT_MJPEG_FPS,
        quality: int = DEFAULT_MJPEG_QUALITY,
    ) -> web.StreamResponse:
        """Transcode the upstream stream to a browser-compatible MJPEG response."""
        try:
            upstream = await self.client.async_open_stream()
        except ValetudoError as err:
            raise HomeAssistantError(f"Unable to open Valetudo stream: {err}") from err

        process: asyncio.subprocess.Process | None = None
        tasks: list[asyncio.Task[Any]] = []
        try:
            process = await asyncio.create_subprocess_exec(
                self._ffmpeg_binary(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "mpegts",
                "-i",
                "pipe:0",
                "-an",
                "-vf",
                f"fps={fps}",
                "-q:v",
                str(quality),
                "-f",
                "mpjpeg",
                "-boundary_tag",
                "ffmpeg",
                "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            input_task = asyncio.create_task(self._pump_input(upstream, process))
            stderr_task = asyncio.create_task(self._read_stderr(process))
            tasks.extend((input_task, stderr_task))
            assert process.stdout is not None

            async with asyncio.timeout(STREAM_START_TIMEOUT):
                first_chunk = await process.stdout.read(_CHUNK_SIZE)
            if not first_chunk:
                await process.wait()
                stderr = await stderr_task
                if reason := _upstream_drop_reason(input_task):
                    raise HomeAssistantError(
                        f"Valetudo closed the stream before a frame arrived: {reason}"
                    )
                details = stderr.decode(errors="replace").strip()
                raise HomeAssistantError(
                    "ffmpeg produced no Valetudo video frames"
                    + (f": {details}" if details else "")
                )
            stream_response = web.StreamResponse(
                headers={"Content-Type": MJPEG_CONTENT_TYPE}
            )
            await stream_response.prepare(request)
            await stream_response.write(first_chunk)
            while chunk := await process.stdout.read(_CHUNK_SIZE):
                await stream_response.write(chunk)
            await process.wait()
            stderr = await stderr_task
            if process.returncode:
                _LOGGER.warning(
                    "ffmpeg exited with status %s: %s",
                    process.returncode,
                    stderr.decode(errors="replace").strip(),
                )
            await stream_response.write_eof()
            return stream_response
        except TimeoutError as err:
            raise HomeAssistantError(
                "Timed out waiting for the Valetudo stream"
            ) from err
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            upstream.release()
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            for task in tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
