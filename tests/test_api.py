"""Tests for the Valetudo HTTP client."""

import base64
import datetime
import ssl
from pathlib import Path

import pytest
from aiohttp import ClientSession, TCPConnector, web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from custom_components.valetudo_camera.api import (
    STREAM_PATH,
    ValetudoAuthError,
    ValetudoCertificateError,
    ValetudoClient,
    ValetudoConnectionError,
    ValetudoNotSupportedError,
    ValetudoStreamingDisabledError,
    ValetudoStreamUnavailableError,
)

pytest_plugins = ("aiohttp.pytest_plugin",)

# Answers of a robot that supports duststreaming; an int stands for an HTTP status.
ROBOT: dict[str, object] = {
    "/api/v2/valetudo": {"systemId": "ExampleSystemId"},
    "/api/v2/robot": {"manufacturer": "Acme", "modelName": "Robot 1"},
    "/api/v2/robot/capabilities": ["BasicControlCapability", "DuststreamingCapability"],
    "/api/v2/valetudo/version": {"release": "2026.08.0"},
    "/api/v2/valetudo/config/interfaces/mqtt": {
        "enabled": True,
        "identity": {"identifier": "robot1"},
    },
}


async def _serve(aiohttp_server, answers: dict[str, object]):
    """Start a fake robot; return its URL and the Authorization headers it saw."""
    authorizations: list[str | None] = []

    async def handle(request: web.Request) -> web.Response:
        authorizations.append(request.headers.get("Authorization"))
        answer = answers.get(request.path, 404)
        if isinstance(answer, int):
            return web.Response(status=answer)
        return web.json_response(answer)

    app = web.Application()
    app.router.add_get("/{path:.*}", handle)
    server = await aiohttp_server(app)
    return str(server.make_url("/")), authorizations


async def test_robot_info_is_read_with_credentials(
    aiohttp_server, socket_enabled
) -> None:
    """The client identifies the robot and authenticates every request."""
    url, authorizations = await _serve(aiohttp_server, ROBOT)

    async with ClientSession() as session:
        info = await ValetudoClient(
            session, url, "user", "pässword"
        ).async_get_robot_info()

    assert (info.system_id, info.manufacturer, info.model) == (
        "ExampleSystemId",
        "Acme",
        "Robot 1",
    )
    assert (info.version, info.mqtt_identifier) == ("2026.08.0", "robot1")
    expected = "Basic " + base64.b64encode("user:pässword".encode()).decode()
    assert set(authorizations) == {expected}


async def test_robot_info_without_mqtt_and_version(
    aiohttp_server, socket_enabled
) -> None:
    """Optional details that Valetudo does not provide are left empty."""
    answers = {
        **ROBOT,
        "/api/v2/valetudo/version": 404,
        "/api/v2/valetudo/config/interfaces/mqtt": {"enabled": False},
    }
    url, authorizations = await _serve(aiohttp_server, answers)

    async with ClientSession() as session:
        info = await ValetudoClient(session, url).async_get_robot_info()

    assert (info.version, info.mqtt_identifier) == (None, None)
    assert set(authorizations) == {None}


@pytest.mark.parametrize(
    ("changed", "error"),
    [
        ({"/api/v2/valetudo": 401}, ValetudoAuthError),
        ({"/api/v2/valetudo": 500}, ValetudoConnectionError),
        ({"/api/v2/valetudo": {"hello": "world"}}, ValetudoConnectionError),
        ({"/api/v2/robot": ["not", "valetudo"]}, ValetudoConnectionError),
        ({"/api/v2/robot/capabilities": 404}, ValetudoNotSupportedError),
        (
            {"/api/v2/robot/capabilities": ["BasicControlCapability"]},
            ValetudoNotSupportedError,
        ),
    ],
    ids=[
        "wrong credentials",
        "server error",
        "not a Valetudo answer",
        "unexpected robot answer",
        "Valetudo too old",
        "robot without camera stream",
    ],
)
async def test_robot_info_failures(
    aiohttp_server, socket_enabled, changed: dict[str, object], error: type[Exception]
) -> None:
    """Each way a URL can fail to be a usable robot is reported distinctly."""
    url, _ = await _serve(aiohttp_server, {**ROBOT, **changed})

    async with ClientSession() as session:
        with pytest.raises(error):
            await ValetudoClient(session, url).async_get_robot_info()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, ValetudoAuthError),
        (403, ValetudoStreamingDisabledError),
        (404, ValetudoNotSupportedError),
        (503, ValetudoStreamUnavailableError),
        (500, ValetudoConnectionError),
    ],
)
async def test_stream_refusals(
    aiohttp_server, socket_enabled, status: int, error: type[Exception]
) -> None:
    """Valetudo's reasons for refusing the stream are told apart."""
    url, _ = await _serve(aiohttp_server, {STREAM_PATH: status})

    async with ClientSession() as session:
        with pytest.raises(error):
            await ValetudoClient(session, url).async_open_stream()


async def test_unreachable_robot(aiohttp_unused_port, socket_enabled) -> None:
    """A robot that is switched off is reported as a connection problem."""
    url = f"http://127.0.0.1:{aiohttp_unused_port()}"

    async with ClientSession() as session:
        client = ValetudoClient(session, url)
        with pytest.raises(ValetudoConnectionError):
            await client.async_get_robot_info()
        with pytest.raises(ValetudoConnectionError):
            await client.async_open_stream()


@pytest.fixture
def self_signed_context(tmp_path: Path) -> ssl.SSLContext:
    """Create a server TLS context whose certificate no client trusts."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(minutes=10))
        .sign(key, hashes.SHA256())
    )
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    return context


async def test_untrusted_certificate_is_reported_separately(
    aiohttp_server, socket_enabled, self_signed_context: ssl.SSLContext
) -> None:
    """A certificate failure is distinguishable from an unreachable robot."""

    async def duststreaming(request: web.Request) -> web.Response:
        return web.json_response({"enabled": True})

    async def properties(request: web.Request) -> web.Response:
        return web.json_response(
            {"duststreamerInstalled": True, "width": 640, "height": 480}
        )

    app = web.Application()
    app.router.add_get("/api/v2/valetudo/config/duststreaming", duststreaming)
    app.router.add_get(
        "/api/v2/robot/capabilities/DuststreamingCapability/properties", properties
    )
    server = await aiohttp_server(app, ssl=self_signed_context)
    url = f"https://127.0.0.1:{server.port}"

    async with ClientSession() as verifying:
        with pytest.raises(ValetudoCertificateError):
            await ValetudoClient(verifying, url).async_get_duststreaming_state()

    async with ClientSession(connector=TCPConnector(ssl=False)) as trusting:
        state = await ValetudoClient(trusting, url).async_get_duststreaming_state()
    assert state.enabled
    await server.close()
