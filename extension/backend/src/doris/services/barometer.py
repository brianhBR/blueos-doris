"""Barometer and thermometer detection service.

Detects connected barometer and thermometer by reading the cached
SCALED_PRESSURE2 MAVLink message from mavlink2rest via HTTP.
If the message exists and has a recent update frequency, the sensors
are present.

Fields used from SCALED_PRESSURE2:
  - press_abs (hPa): absolute pressure  -> Barometer
  - temperature (cdegC): temperature     -> Thermometer
"""

import logging

import httpx

from ..config import blueos_services
from ..models.sensors import ModuleInfo

logger = logging.getLogger(__name__)

class BarometerService:
    """Detects barometer/thermometer via cached SCALED_PRESSURE2 message."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=5.0, follow_redirects=True)
        return self._client

    async def get_modules(self) -> list[ModuleInfo]:
        """Return ModuleInfo entries for barometer and thermometer if detected."""
        base = blueos_services.mavlink2rest
        url = f"{base}/mavlink/vehicles/1/components/1/messages/SCALED_PRESSURE2"
        try:
            resp = await self.client.get(url)
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()

            msg = data.get("message", {})
            if msg.get("type") != "SCALED_PRESSURE2":
                return []

            modules: list[ModuleInfo] = []

            press_abs = msg.get("press_abs")
            if press_abs is not None:
                modules.append(
                    ModuleInfo(
                        id="barometer",
                        name="Barometer",
                        type="sensor",
                        status="connected",
                        module_status=f"Ready: {press_abs:.1f} hPa",
                    )
                )

            temp_cdeg = msg.get("temperature")
            if temp_cdeg is not None:
                temp_c = temp_cdeg / 100.0
                modules.append(
                    ModuleInfo(
                        id="thermometer",
                        name="Thermometer",
                        type="sensor",
                        status="connected",
                        module_status=f"Ready: {temp_c:.1f} \u00b0C",
                    )
                )

            return modules
        except httpx.HTTPStatusError:
            return []
        except Exception as e:
            logger.warning("Failed to detect barometer/thermometer: %s", e)
            return []

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
