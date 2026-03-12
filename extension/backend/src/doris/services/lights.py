"""Light detection service.

Detects configured lights by reading SERVOx_FUNCTION MAVLink parameters
via mavlink2rest WebSocket. Uses the ``?filter=PARAM_VALUE`` query to
receive only PARAM_VALUE messages, avoiding polling and HTTP round-trips.

Reference: BlueOS GenericViewer.vue light detection logic.
"""

import asyncio
import json
import logging

import websockets

from ..config import blueos_services
from ..models.sensors import ModuleInfo

logger = logging.getLogger(__name__)

SERVO_FUNCTION_RCIN9 = 59  # Lights 1
SERVO_FUNCTION_RCIN10 = 60  # Lights 2

LIGHT_FUNCTIONS: dict[int, str] = {
    SERVO_FUNCTION_RCIN9: "Lights 1",
    SERVO_FUNCTION_RCIN10: "Lights 2",
}

MAX_SERVO_CHANNELS = 16
WS_TIMEOUT = 5.0


def _mavlink2rest_ws_url() -> str:
    """Build the WebSocket URL for mavlink2rest with PARAM_VALUE filter."""
    base = blueos_services.mavlink2rest
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    return f"{ws_base}/ws/mavlink?filter=PARAM_VALUE"


def _build_param_request(param_name: str) -> str:
    """Build a PARAM_REQUEST_READ MAVLink message as JSON."""
    return json.dumps({
        "header": {"system_id": 255, "component_id": 0, "sequence": 0},
        "message": {
            "type": "PARAM_REQUEST_READ",
            "target_system": 1,
            "target_component": 1,
            "param_id": list(param_name.ljust(16, "\x00")),
            "param_index": -1,
        },
    })


def _parse_param_value(raw: str) -> tuple[str, float] | None:
    """Extract (param_name, value) from a PARAM_VALUE WebSocket message."""
    if not raw.startswith("{"):
        return None
    try:
        data = json.loads(raw)
        message = data.get("message", {})
        if message.get("type") != "PARAM_VALUE":
            return None
        param_id = "".join(c for c in message.get("param_id", []) if c != "\x00")
        return (param_id, message.get("param_value"))
    except (json.JSONDecodeError, TypeError):
        return None


class LightService:
    """Detects light modules by querying MAVLink servo parameters over WebSocket."""

    async def get_light_modules(self) -> list[ModuleInfo]:
        """Return a ModuleInfo for each detected light output."""
        try:
            param_values = await self._fetch_servo_params()
        except Exception as e:
            logger.warning(f"Failed to detect lights: {e}")
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
        """Open a WebSocket, request all SERVOx_FUNCTION params, collect responses."""
        ws_url = _mavlink2rest_ws_url()
        expected: set[str] = {f"SERVO{ch}_FUNCTION" for ch in range(1, MAX_SERVO_CHANNELS + 1)}
        results: dict[str, float] = {}

        async with websockets.connect(ws_url, close_timeout=2) as ws:
            for param_name in sorted(expected):
                await ws.send(_build_param_request(param_name))

            try:
                async with asyncio.timeout(WS_TIMEOUT):
                    while len(results) < len(expected):
                        raw = await ws.recv()
                        parsed = _parse_param_value(raw)
                        if parsed is None:
                            continue
                        name, value = parsed
                        if name in expected:
                            results[name] = value
            except TimeoutError:
                logger.debug(
                    f"WebSocket timeout after {WS_TIMEOUT}s, "
                    f"received {len(results)}/{len(expected)} params"
                )

        return results

    async def close(self) -> None:
        """No persistent resources to close (WebSocket is per-request)."""
