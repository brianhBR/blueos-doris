"""Light detection service.

Detects configured lights by reading SERVOx_FUNCTION MAVLink parameters
via mavlink2rest HTTP API.  Sends individual PARAM_REQUEST_READ messages
and polls the cached PARAM_VALUE response, using the message counter to
detect when a fresh reply arrives.

Falls back gracefully when the autopilot doesn't respond within the
timeout — lights simply won't appear in the module list.
"""

import asyncio
import logging

import httpx

from ..config import blueos_services
from ..models.sensors import ModuleInfo

logger = logging.getLogger(__name__)

SERVO_FUNCTION_RCIN9 = 181   # Lights 1
SERVO_FUNCTION_RCIN10 = 182  # Lights 2

LIGHT_FUNCTIONS: dict[int, str] = {
    SERVO_FUNCTION_RCIN9: "Lights 1",
    SERVO_FUNCTION_RCIN10: "Lights 2",
}

MAX_SERVO_CHANNELS = 16
PARAM_POLL_INTERVAL = 0.05
PARAM_POLL_TIMEOUT = 0.4
OVERALL_TIMEOUT = 8.0


class LightService:
    """Detects light modules by querying MAVLink servo parameters over HTTP."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=5.0, follow_redirects=True)
        return self._client

    async def get_light_modules(self) -> list[ModuleInfo]:
        """Return a ModuleInfo for each detected light output."""
        try:
            param_values = await self._fetch_servo_params()
        except Exception as e:
            logger.warning("Failed to detect lights: %s", e)
            return []

        modules: list[ModuleInfo] = []
        for channel in range(1, MAX_SERVO_CHANNELS + 1):
            param_name = f"SERVO{channel}_FUNCTION"
            value = param_values.get(param_name)
            if value is None:
                continue
            func_int = int(value)
            if func_int in LIGHT_FUNCTIONS:
                label = LIGHT_FUNCTIONS[func_int]
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

    async def _fetch_servo_params(self) -> dict[str, float]:
        """Request all SERVOx_FUNCTION params via HTTP, one at a time."""
        base = blueos_services.mavlink2rest
        cache_url = f"{base}/mavlink/vehicles/1/components/1/messages/PARAM_VALUE"
        post_url = f"{base}/mavlink"
        results: dict[str, float] = {}

        baseline_counter = await self._get_param_counter(cache_url)

        try:
            async with asyncio.timeout(OVERALL_TIMEOUT):
                for ch in range(1, MAX_SERVO_CHANNELS + 1):
                    param_name = f"SERVO{ch}_FUNCTION"
                    result = await self._request_single_param(
                        param_name, post_url, cache_url, baseline_counter
                    )
                    if result is not None:
                        name, value = result
                        results[name] = value
                        baseline_counter += 1
        except TimeoutError:
            logger.debug(
                "Light param fetch timed out after %.0fs, got %d/%d",
                OVERALL_TIMEOUT, len(results), MAX_SERVO_CHANNELS,
            )

        return results

    async def _request_single_param(
        self,
        param_name: str,
        post_url: str,
        cache_url: str,
        baseline_counter: int,
    ) -> tuple[str, float] | None:
        """Send PARAM_REQUEST_READ and poll for the response."""
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
            await self.client.post(post_url, json=payload)
        except Exception as e:
            logger.debug("Failed to send PARAM_REQUEST_READ for %s: %s", param_name, e)
            return None

        elapsed = 0.0
        while elapsed < PARAM_POLL_TIMEOUT:
            await asyncio.sleep(PARAM_POLL_INTERVAL)
            elapsed += PARAM_POLL_INTERVAL
            try:
                resp = await self.client.get(cache_url)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                counter = data.get("status", {}).get("time", {}).get("counter", 0)
                if counter <= baseline_counter:
                    continue
                msg = data.get("message", {})
                if msg.get("type") != "PARAM_VALUE":
                    continue
                pid = "".join(c for c in msg.get("param_id", []) if c != "\x00")
                return (pid, msg.get("param_value", 0.0))
            except Exception:
                continue
        return None

    async def _get_param_counter(self, cache_url: str) -> int:
        """Read the current PARAM_VALUE message counter."""
        try:
            resp = await self.client.get(cache_url)
            if resp.status_code != 200:
                return 0
            data = resp.json()
            return data.get("status", {}).get("time", {}).get("counter", 0)
        except Exception:
            return 0

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
