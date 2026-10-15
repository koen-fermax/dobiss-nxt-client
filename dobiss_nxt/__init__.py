"""Asynchronous access to the Dobiss NXT REST API."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
import jwt
from yarl import URL


class DobissError(Exception):
    """Base error for Dobiss communication."""


class DobissAuthenticationError(DobissError):
    """The controller rejected the API secret."""


class DobissConnectionError(DobissError):
    """The controller could not be reached."""


class DobissProtocolError(DobissError):
    """The controller returned an unexpected response."""


@dataclass(frozen=True, slots=True)
class Light:
    """A physical output identified by module address and channel."""

    address: int
    channel: int
    name: str
    dimmable: bool
    group: str

    @property
    def key(self) -> str:
        """Return the identifier within a controller."""
        return f"{self.address}_{self.channel}"


def normalize_host(host: str) -> str:
    """Validate a local hostname or IP address, optionally including a port."""
    if not host or host != host.strip() or any(c.isspace() for c in host):
        raise ValueError("Enter a hostname or IP address")
    url = URL(f"http://{host}")
    if (
        not url.host
        or url.user is not None
        or url.password is not None
        or url.path != "/"
        or url.query_string
        or url.fragment
        or "://" in host
    ):
        raise ValueError("Enter a hostname or IP address without a URL path")
    _ = url.port
    return url.raw_authority.lower()


class DobissClient:
    """Client using a caller-owned session and explicit transport selection."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        secret: str,
        *,
        secure: bool = False,
    ) -> None:
        """Initialize without opening connections."""
        self.host = normalize_host(host)
        if not secret:
            raise ValueError("API secret must not be empty")
        self._session = session
        self._secret = secret
        self._base_url = URL(
            f"{'https' if secure else 'http'}://{self.host}/api/local/"
        )

    async def _request(
        self, method: str, endpoint: str, payload: dict[str, int] | None = None
    ) -> Any:
        now = datetime.now(UTC)
        token = jwt.encode(
            {"name": "home_assistant", "iat": now, "exp": now + timedelta(minutes=5)},
            self._secret,
            algorithm="HS256",
        )
        try:
            async with self._session.request(
                method,
                self._base_url / endpoint,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
                raise_for_status=False,
            ) as response:
                if response.status in (401, 403):
                    raise DobissAuthenticationError("API authentication failed")
                if response.status >= 500:
                    raise DobissConnectionError("Controller temporarily unavailable")
                if response.status != 200:
                    raise DobissProtocolError(
                        f"Unexpected HTTP status {response.status}"
                    )
                try:
                    return await response.json()
                except (ValueError, aiohttp.ContentTypeError) as err:
                    raise DobissProtocolError("Expected a JSON response") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise DobissConnectionError(
                "Unable to communicate with controller"
            ) from err

    async def discover_lights(self) -> tuple[Light, ...]:
        """Discover conventional lights without treating other outputs as lights."""
        response = await self._request("GET", "discover")
        lights: dict[str, Light] = {}
        try:
            for group in response["groups"]:
                for subject in group["subjects"]:
                    if int(subject["type"]) not in (0, 4, 8, 16, 24):
                        continue
                    if int(subject["icons_id"]) not in (0, 6):
                        continue
                    dimmable = subject["dimmable"]
                    if not isinstance(dimmable, bool):
                        raise TypeError("Invalid dimmable flag")
                    light = Light(
                        address=int(subject["address"]),
                        channel=int(subject["channel"]),
                        name=str(subject["name"]),
                        dimmable=dimmable,
                        group=str(group["group"]["name"]),
                    )
                    if light.address < 0 or light.channel < 0:
                        raise ValueError("Invalid output address")
                    # A named group has better metadata than the ungrouped listing.
                    if light.key not in lights or int(group["group"]["id"]) != 0:
                        lights[light.key] = light
        except (KeyError, TypeError, ValueError) as err:
            raise DobissProtocolError("Invalid discovery response") from err
        return tuple(lights.values())

    async def light_states(self, lights: tuple[Light, ...]) -> dict[str, int | None]:
        """Read all outputs in one request; missing outputs remain unavailable."""
        response = await self._request("GET", "status")
        if isinstance(response, dict) and "status" in response:
            response = response["status"]
        if not isinstance(response, dict):
            raise DobissProtocolError("Invalid status response")
        states: dict[str, int | None] = {}
        try:
            for light in lights:
                module = response.get(str(light.address), {})
                if isinstance(module, list):
                    value = (
                        module[light.channel] if light.channel < len(module) else None
                    )
                elif isinstance(module, dict):
                    value = module.get(str(light.channel))
                else:
                    raise TypeError("Invalid module state")
                if isinstance(value, dict):
                    value = value["status"]
                value = int(value) if value is not None else None
                if value is not None and not 0 <= value <= 100:
                    raise ValueError("Invalid light state")
                states[light.key] = value
        except (KeyError, TypeError, ValueError) as err:
            raise DobissProtocolError("Invalid light status") from err
        return states

    async def set_light(
        self, light: Light, *, on: bool, brightness: int | None = None
    ) -> None:
        """Set an output, with optional brightness from 0 to 100."""
        payload = {
            "address": light.address,
            "channel": light.channel,
            "action": int(on),
        }
        if brightness is not None:
            if not light.dimmable or not 0 <= brightness <= 100:
                raise ValueError(
                    "Brightness requires a dimmable output and range 0..100"
                )
            payload["option1"] = brightness
        await self._request("POST", "action", payload)
