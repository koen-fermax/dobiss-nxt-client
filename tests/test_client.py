"""Verify the wire protocol with synthetic, credential-free fixtures."""

from collections.abc import AsyncGenerator, Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import jwt
import pytest

from dobiss_nxt import (
    DobissAuthenticationError,
    DobissClient,
    DobissConnectionError,
    DobissProtocolError,
    Light,
    normalize_host,
)

SECRET = "synthetic-test-secret-not-a-real-key-123456"
LIGHT = Light(4, 0, "Kitchen", True, "Ground floor")
DISCOVERY = {
    "groups": [
        {
            "group": {"id": "1", "name": "Ground floor"},
            "subjects": [
                {
                    "address": "4",
                    "channel": "0",
                    "name": "Kitchen",
                    "type": "16",
                    "icons_id": "0",
                    "dimmable": True,
                },
                {
                    "address": "2",
                    "channel": "0",
                    "name": "Socket",
                    "type": "8",
                    "icons_id": "1",
                    "dimmable": False,
                },
                {
                    "address": "201",
                    "channel": "1",
                    "name": "Scene",
                    "type": "201",
                    "icons_id": "0",
                    "dimmable": False,
                },
            ],
        }
    ]
}


class Responses:
    """Stub the public request context manager without aiohttp internals."""

    def __init__(self) -> None:
        self.requests: dict[tuple[str, str], list[SimpleNamespace]] = {}
        self.routes: dict[tuple[str, str], dict[str, Any]] = {}

    def get(self, url: str, **kwargs: Any) -> None:
        self.routes[("GET", url)] = kwargs

    def post(self, url: str, **kwargs: Any) -> None:
        self.routes[("POST", url)] = kwargs

    def request(
        self, session: aiohttp.ClientSession, method: str, url: object, **kwargs: Any
    ) -> MagicMock:
        key = (method, str(url))
        route = self.routes[key]
        self.requests.setdefault(key, []).append(SimpleNamespace(kwargs=kwargs))
        response = AsyncMock(spec=aiohttp.ClientResponse)
        response.status = route.get("status", 200)
        response.json.return_value = route.get("payload")
        if "body" in route:
            response.json.side_effect = ValueError("Invalid JSON")
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        if "exception" in route:
            context.__aenter__.side_effect = route["exception"]
        return context


@pytest.fixture
def responses() -> Generator[Responses]:
    """Intercept network requests."""
    mock = Responses()
    with patch.object(
        aiohttp.ClientSession, "request", autospec=True, side_effect=mock.request
    ):
        yield mock


@pytest.fixture
async def client() -> AsyncGenerator[DobissClient]:
    """Use a real session, owned by the caller."""
    async with aiohttp.ClientSession() as session:
        yield DobissClient(session, "dobiss.local", SECRET)
        assert not session.closed


async def test_discovery_and_token(client: DobissClient, responses: Responses) -> None:
    """Verify classification and a correctly expiring JWT."""
    responses.get("http://dobiss.local/api/local/discover", payload=DISCOVERY)
    assert await client.discover_lights() == (LIGHT,)
    request = next(iter(responses.requests.values()))[0]
    token = request.kwargs["headers"]["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, SECRET, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 300
    assert claims["name"] == "home_assistant"
    assert request.kwargs["allow_redirects"] is False
    assert request.kwargs["timeout"].total == 10


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"status": {"4": [50]}}, 50),
        ({"4": {"0": "20"}}, 20),
        ({"4": {"0": {"status": "1"}}}, 1),
        ({"4": [0]}, 0),
        ({}, None),
        ({"4": []}, None),
    ],
)
async def test_states(
    client: DobissClient, responses: Responses, payload: dict, expected: int | None
) -> None:
    """Handle wrapped and direct module status, including missing outputs."""
    responses.get("http://dobiss.local/api/local/status", payload=payload)
    assert await client.light_states((LIGHT,)) == {"4_0": expected}


@pytest.mark.parametrize(
    ("on", "brightness", "expected"),
    [
        (True, 42, {"address": 4, "channel": 0, "action": 1, "option1": 42}),
        (False, None, {"address": 4, "channel": 0, "action": 0}),
    ],
)
async def test_action(
    client: DobissClient,
    responses: Responses,
    on: bool,
    brightness: int | None,
    expected: dict,
) -> None:
    """Verify exact action payloads."""
    responses.post("http://dobiss.local/api/local/action", payload={"new_status": 42})
    await client.set_light(LIGHT, on=on, brightness=brightness)
    assert next(iter(responses.requests.values()))[0].kwargs["json"] == expected


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, DobissAuthenticationError),
        (403, DobissAuthenticationError),
        (503, DobissConnectionError),
        (302, DobissProtocolError),
        (404, DobissProtocolError),
    ],
)
async def test_http_errors(
    client: DobissClient, responses: Responses, status: int, error: type[Exception]
) -> None:
    """Distinguish credentials, availability and unsupported responses."""
    responses.get("http://dobiss.local/api/local/discover", status=status)
    with pytest.raises(error):
        await client.discover_lights()


@pytest.mark.parametrize("error", [aiohttp.ClientConnectionError(), TimeoutError()])
async def test_network_errors(
    client: DobissClient, responses: Responses, error: Exception
) -> None:
    """Expose network failures consistently."""
    responses.get("http://dobiss.local/api/local/discover", exception=error)
    with pytest.raises(DobissConnectionError):
        await client.discover_lights()


@pytest.mark.parametrize(
    "payload", [[], {"status": []}, {"4": [101]}, {"4": ["bad"]}, {"4": "bad"}]
)
async def test_bad_status(
    client: DobissClient, responses: Responses, payload: object
) -> None:
    """Do not turn malformed statuses into plausible state."""
    responses.get("http://dobiss.local/api/local/status", payload=payload)
    with pytest.raises(DobissProtocolError):
        await client.light_states((LIGHT,))


async def test_bad_json(client: DobissClient, responses: Responses) -> None:
    """Reject non-JSON responses."""
    responses.get("http://dobiss.local/api/local/discover", body="<html>error</html>")
    with pytest.raises(DobissProtocolError):
        await client.discover_lights()


@pytest.mark.parametrize(
    "host",
    [
        "",
        "http://dobiss.local",
        "dobiss.local/path",
        "user:pass@dobiss.local",
        "dobiss.local?secret=yes",
        "dobiss.local#fragment",
        " dobiss.local",
        "dobiss.local:bad",
    ],
)
def test_invalid_host(host: str) -> None:
    """Reject URL components that could redirect or leak credentials."""
    with pytest.raises(ValueError):
        normalize_host(host)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("DOBISS.local", "dobiss.local"),
        ("192.168.1.2:8080", "192.168.1.2:8080"),
        ("[::1]:80", "[::1]:80"),
    ],
)
def test_valid_host(host: str, expected: str) -> None:
    """Support local hostnames, IPv4 and bracketed IPv6."""
    assert normalize_host(host) == expected


async def test_https(responses: Responses) -> None:
    """Use HTTPS when explicitly selected."""
    responses.get("https://dobiss.local/api/local/discover", payload={"groups": []})
    async with aiohttp.ClientSession() as session:
        client = DobissClient(session, "dobiss.local", SECRET, secure=True)
        assert await client.discover_lights() == ()


@pytest.mark.parametrize("brightness", [-1, 101])
async def test_invalid_brightness(client: DobissClient, brightness: int) -> None:
    """Reject invalid output levels before any network call."""
    with pytest.raises(ValueError):
        await client.set_light(LIGHT, on=True, brightness=brightness)


async def test_non_dimmable(client: DobissClient) -> None:
    """Do not send brightness to a relay."""
    with pytest.raises(ValueError):
        await client.set_light(
            Light(2, 0, "Relay", False, "Room"), on=True, brightness=50
        )
