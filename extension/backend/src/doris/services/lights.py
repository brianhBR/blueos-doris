"""Light detection service.

Detects configured lights by checking the SERVOn_FUNCTION parameter
via mavlink2rest.  ArduSub conventionally maps Lights 1 to SERVO13
(driven by RC9 input, function 59).

When the vehicle is disarmed with no joystick connected the live PWM
output is 0 even if lights are configured, so we fall back to reading
the servo function parameter to decide whether a light channel exists.
"""

import asyncio
import logging

import httpx

from ..config import blueos_services
from ..models.sensors import ModuleInfo

logger = logging.getLogger(__name__)

LIGHT_CHANNELS: dict[int, str] = {
    13: "Lights 1",
}

# ArduSub RCIN passthrough functions: RCIN1=51 … RCIN16=66
_RCIN_FUNCTIONS = set(range(51, 67))


class LightService:
    """Detects light modules via servo function parameters."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._fn_cache: dict[int, bool] = {}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=5.0, follow_redirects=True)
        return self._client

    async def _is_servo_configured(self, channel: int) -> bool:
        """Check if SERVOn_FUNCTION is set to an RCIN passthrough.

        Only caches positive results so a temporary non-RCIN value
        (e.g. during frame application before Lua overrides it)
        doesn't permanently suppress detection.
        """
        if self._fn_cache.get(channel):
            return True

        base = blueos_services.mavlink2rest
        param_name = f"SERVO{channel}_FUNCTION"
        payload = {
            "header": {"system_id": 255, "component_id": 0, "sequence": 0},
            "message": {
                "type": "PARAM_REQUEST_READ",
                "target_system": 1,
                "target_component": 1,
                "param_id": list(param_name.ljust(16, "\x00")),
                "param_index": -1,
            },
        }
        try:
            resp = await self.client.post(f"{base}/mavlink", json=payload)
            resp.raise_for_status()
            await asyncio.sleep(0.4)

            resp = await self.client.get(
                f"{base}/mavlink/vehicles/1/components/1/messages/PARAM_VALUE"
            )
            resp.raise_for_status()
            data = resp.json()

            returned_name = "".join(
                c for c in data.get("message", {}).get("param_id", [])
                if c != "\x00"
            )
            if returned_name == param_name:
                value = int(data["message"].get("param_value", 0))
                configured = value in _RCIN_FUNCTIONS
                if configured:
                    self._fn_cache[channel] = True
                logger.info("%s = %d → %s", param_name, value,
                            "light detected" if configured else "not a light")
                return configured
        except Exception as e:
            logger.debug("Could not read %s: %s", param_name, e)
        return False

    async def get_light_modules(self) -> list[ModuleInfo]:
        """Return a ModuleInfo for each detected light output."""
        base = blueos_services.mavlink2rest
        modules: list[ModuleInfo] = []

        servo_msg: dict = {}
        url = f"{base}/mavlink/vehicles/1/components/1/messages/SERVO_OUTPUT_RAW"
        try:
            resp = await self.client.get(url)
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            servo_msg = resp.json().get("message", {})
        except Exception as e:
            logger.debug("SERVO_OUTPUT_RAW unavailable: %s", e)
            return []

        for channel, label in LIGHT_CHANNELS.items():
            pwm = servo_msg.get(f"servo{channel}_raw", 0)
            if pwm > 0:
                modules.append(
                    ModuleInfo(
                        id=f"light-ch{channel}",
                        name=f"{label} (SERVO{channel})",
                        type="light",
                        status="connected",
                        module_status=f"Ready: PWM {pwm}",
                    )
                )
                continue

            if await self._is_servo_configured(channel):
                modules.append(
                    ModuleInfo(
                        id=f"light-ch{channel}",
                        name=f"{label} (SERVO{channel})",
                        type="light",
                        status="connected",
                        module_status="Ready: Idle",
                    )
                )

        return modules

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
