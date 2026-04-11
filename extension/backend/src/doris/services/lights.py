"""Light detection service.

Detects configured lights by reading SERVO_OUTPUT_RAW from mavlink2rest
via HTTP.  ArduSub conventionally maps Lights 1 to SERVO13 (RC9 input).

The previous WebSocket-based param query approach broke with
mavlink2rest v0.11+ which changed the WebSocket API.
"""

import logging

import httpx

from ..config import blueos_services
from ..models.sensors import ModuleInfo

logger = logging.getLogger(__name__)

LIGHT_CHANNELS: dict[int, str] = {
    13: "Lights 1",
}


class LightService:
    """Detects light modules from SERVO_OUTPUT_RAW via HTTP."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=5.0, follow_redirects=True)
        return self._client

    async def get_light_modules(self) -> list[ModuleInfo]:
        """Return a ModuleInfo for each detected light output."""
        base = blueos_services.mavlink2rest
        url = f"{base}/mavlink/vehicles/1/components/1/messages/SERVO_OUTPUT_RAW"
        try:
            resp = await self.client.get(url)
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()

            msg = data.get("message", {})
            modules: list[ModuleInfo] = []
            for channel, label in LIGHT_CHANNELS.items():
                pwm = msg.get(f"servo{channel}_raw", 0)
                if pwm > 0:
                    modules.append(
                        ModuleInfo(
                            id=f"light-ch{channel}",
                            name=f"{label} (SERVO{channel})",
                            type="light",
                            status="connected",
                            module_status="Ready: Active",
                        )
                    )
            return modules
        except httpx.HTTPStatusError:
            return []
        except Exception as e:
            logger.warning("Failed to detect lights: %s", e)
            return []

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
