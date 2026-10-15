# Dobiss NXT client

Async REST client for the [manufacturer API](https://support.dobiss.com/books/nl-dobiss-nxt/page/developer-api).
The caller owns the aiohttp session. Requests have a 10-second timeout, use short-lived
HS256 JWTs and do not follow redirects with credentials. Network, authentication and
protocol failures have separate exception classes. Only conventional light outputs
(module types 0, 4, 8, 16, 24 with light/table-lamp icons) are supported initially.

Source and an enabled issue tracker are available on
[GitHub](https://github.com/koen-fermax/dobiss-nxt-client).
The package is available on [PyPI](https://pypi.org/project/dobiss-nxt-client/).
Install it with `pip install dobiss-nxt-client`.

```python
from aiohttp import ClientSession
from dobiss_nxt import DobissClient


async def read_lights(host: str, secret: str) -> None:
    async with ClientSession() as session:
        client = DobissClient(session, host, secret)
        lights = await client.discover_lights()
        states = await client.light_states(lights)
        print(states)
```

Automated tests use synthetic API examples and a loopback HTTP server. The user
additionally verified discovery, status, on/off control, dimming and restoration
on a real NXT v4.40 installation. Full operation inside Home Assistant remains to
be verified before submitting the integration.
