"""Exercise actual HTTP transport against a local simulated controller."""

from collections.abc import AsyncGenerator

import aiohttp
import jwt
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from dobiss_nxt import DobissAuthenticationError, DobissClient, Light

SECRET = "synthetic-test-secret-not-a-real-key-123456"


@pytest.fixture
async def controller() -> AsyncGenerator[TestServer]:
    """Start a real loopback server that validates JWT and action parameters."""
    state = {"value": 0}

    @web.middleware
    async def authenticate(request: web.Request, handler: object) -> web.StreamResponse:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        try:
            jwt.decode(
                token, SECRET, algorithms=["HS256"], options={"require": ["exp", "iat"]}
            )
        except jwt.InvalidTokenError:
            return web.json_response({}, status=401)
        return await handler(request)

    async def discovery(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "groups": [
                    {
                        "group": {"id": 1, "name": "Room"},
                        "subjects": [
                            {
                                "address": 4,
                                "channel": 0,
                                "name": "Lamp",
                                "type": 16,
                                "icons_id": 0,
                                "dimmable": True,
                            }
                        ],
                    }
                ]
            }
        )

    async def status(request: web.Request) -> web.Response:
        return web.json_response({"status": {"4": [state["value"]]}})

    async def action(request: web.Request) -> web.Response:
        payload = await request.json()
        assert payload["address"] == 4
        assert payload["channel"] == 0
        state["value"] = payload.get("option1", payload["action"] * 100)
        return web.json_response({"new_status": state["value"]})

    app = web.Application(middlewares=[authenticate])
    app.router.add_get("/api/local/discover", discovery)
    app.router.add_get("/api/local/status", status)
    app.router.add_post("/api/local/action", action)
    async with TestServer(app) as server:
        yield server


async def test_round_trip(controller: TestServer) -> None:
    """Discover, command and read back a dimmer using real aiohttp I/O."""
    async with aiohttp.ClientSession() as session:
        client = DobissClient(session, f"127.0.0.1:{controller.port}", SECRET)
        lights = await client.discover_lights()
        assert lights == (Light(4, 0, "Lamp", True, "Room"),)
        assert await client.light_states(lights) == {"4_0": 0}
        await client.set_light(lights[0], on=True, brightness=75)
        assert await client.light_states(lights) == {"4_0": 75}
        await client.set_light(lights[0], on=False)
        assert await client.light_states(lights) == {"4_0": 0}


async def test_rejected_secret(controller: TestServer) -> None:
    """Reject a JWT signed with a different secret."""
    async with aiohttp.ClientSession() as session:
        client = DobissClient(
            session,
            f"127.0.0.1:{controller.port}",
            "wrong-synthetic-secret-1234567890123456",
        )
        with pytest.raises(DobissAuthenticationError):
            await client.discover_lights()
