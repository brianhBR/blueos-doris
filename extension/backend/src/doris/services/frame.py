"""Vehicle frame configuration service.

Manages the DORIS frame definition: a set of ArduPilot parameters that
configure a thrusterless deep-ocean lander with lights on SERVO13 and
a drop-weight relay on SERVO14.

The service can pull all current vehicle parameters via mavlink2rest,
apply the DORIS frame overrides, and report frame status.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import websockets

from ..config import blueos_services

logger = logging.getLogger(__name__)

DATA_ROOT = Path(os.environ.get("DORIS_DATA_ROOT", "/tmp/storage"))
FRAME_SENTINEL = DATA_ROOT / "configurations" / ".doris_frame_applied"

FRAME_DIR = Path(__file__).resolve().parents[3] / "frames"
FRAME_SEARCH_PATHS = [
    Path("/app/frames"),
    FRAME_DIR,
]

PARAM_FETCH_TIMEOUT = 30.0
PARAM_SET_DELAY = 0.05
STARTUP_RETRY_DELAY = 3.0
STARTUP_MAX_RETRIES = 10

DEFAULT_KEY_PARAMS = [
    "AHRS_ORIENTATION",
    "FRAME_CONFIG",
    "RELAY1_FUNCTION",
    "RELAY1_PIN",
    "SCR_ENABLE",
    "BATT_CAPACITY",
    "BATT_MONITOR",
    "BATT_VOLT_PIN",
    "BATT_CURR_PIN",
    "FS_LEAK_ENABLE",
    "FS_PRESS_ENABLE",
    "FS_TEMP_ENABLE",
    *(f"SERVO{ch}_FUNCTION" for ch in range(1, 17)),
    "SERVO13_MIN",
    "SERVO13_MAX",
    "SERVO13_TRIM",
    "SERVO14_MIN",
    "SERVO14_MAX",
]


def _mavlink2rest_ws_url() -> str:
    base = blueos_services.mavlink2rest
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    return f"{ws_base}/ws/mavlink?filter=PARAM_VALUE"


def _mavlink2rest_http_url() -> str:
    return blueos_services.mavlink2rest


def _parse_param_value(raw: str) -> tuple[str, float, int] | None:
    """Extract (param_name, value, param_count) from a PARAM_VALUE message."""
    if not raw.startswith("{"):
        return None
    try:
        data = json.loads(raw)
        message = data.get("message", {})
        if message.get("type") != "PARAM_VALUE":
            return None
        param_id = "".join(c for c in message.get("param_id", []) if c != "\x00")
        value = message.get("param_value", 0.0)
        count = message.get("param_count", 0)
        return (param_id, float(value), int(count))
    except (json.JSONDecodeError, TypeError):
        return None


class FrameService:
    """Manages vehicle frame configuration for DORIS."""

    def load_frame_definition(self, name: str = "doris") -> dict | None:
        """Load a frame definition JSON from the frames directory."""
        for search_path in FRAME_SEARCH_PATHS:
            path = search_path / f"{name}.json"
            if path.is_file():
                try:
                    return json.loads(path.read_text())
                except (json.JSONDecodeError, OSError) as e:
                    logger.error("Failed to load frame %s from %s: %s", name, path, e)
                    return None
        logger.warning("Frame definition '%s' not found in %s", name, FRAME_SEARCH_PATHS)
        return None

    async def fetch_vehicle_params(self) -> dict[str, float]:
        """Pull all parameters from the connected vehicle via PARAM_REQUEST_LIST.

        Returns a dict mapping parameter names to their current values.
        """
        ws_url = _mavlink2rest_ws_url()
        results: dict[str, float] = {}
        expected_count: int | None = None

        try:
            async with websockets.connect(ws_url, close_timeout=5) as ws:
                await ws.send(json.dumps({
                    "header": {"system_id": 255, "component_id": 0, "sequence": 0},
                    "message": {
                        "type": "PARAM_REQUEST_LIST",
                        "target_system": 1,
                        "target_component": 1,
                    },
                }))

                try:
                    async with asyncio.timeout(PARAM_FETCH_TIMEOUT):
                        while True:
                            raw = await ws.recv()
                            parsed = _parse_param_value(raw)
                            if parsed is None:
                                continue
                            name, value, count = parsed
                            if expected_count is None and count > 0:
                                expected_count = count
                            results[name] = value
                            if expected_count and len(results) >= expected_count:
                                break
                except TimeoutError:
                    logger.warning(
                        "Timeout fetching vehicle params: got %d/%s",
                        len(results),
                        expected_count or "?",
                    )
        except Exception as e:
            logger.error("Failed to connect to mavlink2rest WebSocket: %s", e)

        logger.info("Fetched %d vehicle parameters", len(results))
        return results

    def _get_critical_param_names(self) -> set[str]:
        """Build the set of parameter names to check from frame definition + defaults."""
        frame = self.load_frame_definition()
        critical = set(DEFAULT_KEY_PARAMS)
        if frame:
            critical.update(frame.get("critical_params", []))
        return critical

    async def fetch_key_params(self) -> dict[str, float]:
        """Fetch only the key frame-related parameters from the vehicle."""
        ws_url = _mavlink2rest_ws_url()
        results: dict[str, float] = {}
        expected = self._get_critical_param_names()

        try:
            async with websockets.connect(ws_url, close_timeout=5) as ws:
                for param_name in sorted(expected):
                    await ws.send(json.dumps({
                        "header": {"system_id": 255, "component_id": 0, "sequence": 0},
                        "message": {
                            "type": "PARAM_REQUEST_READ",
                            "target_system": 1,
                            "target_component": 1,
                            "param_id": list(param_name.ljust(16, "\x00")),
                            "param_index": -1,
                        },
                    }))

                try:
                    async with asyncio.timeout(10.0):
                        while len(results) < len(expected):
                            raw = await ws.recv()
                            parsed = _parse_param_value(raw)
                            if parsed is None:
                                continue
                            name, value, _ = parsed
                            if name in expected:
                                results[name] = value
                except TimeoutError:
                    logger.debug(
                        "Timeout fetching key params: got %d/%d",
                        len(results),
                        len(expected),
                    )
        except Exception as e:
            logger.error("Failed to fetch key params: %s", e)

        return results

    async def apply_frame(self, frame_name: str = "doris") -> dict:
        """Apply a frame definition to the connected vehicle.

        Loads the frame JSON and pushes each parameter via PARAM_SET.
        Returns a summary with success/failure counts.
        """
        frame = self.load_frame_definition(frame_name)
        if frame is None:
            return {"success": False, "message": f"Frame '{frame_name}' not found"}

        params = frame.get("parameters", {})
        if not params:
            return {"success": False, "message": "Frame has no parameters"}

        succeeded = []
        failed = []

        for name, value in params.items():
            ok = await self._set_param(name, float(value))
            if ok:
                succeeded.append(name)
            else:
                failed.append(name)
            await asyncio.sleep(PARAM_SET_DELAY)

        logger.info(
            "Frame '%s' applied: %d succeeded, %d failed",
            frame_name,
            len(succeeded),
            len(failed),
        )

        return {
            "success": len(failed) == 0,
            "frame": frame_name,
            "total": len(params),
            "succeeded": len(succeeded),
            "failed": len(failed),
            "failed_params": failed,
        }

    def _compare_param(self, actual: float, expected: float) -> bool:
        """Compare a vehicle param against expected value, tolerant of float precision."""
        if isinstance(expected, int) or expected == int(expected):
            return int(actual) == int(expected)
        return abs(actual - expected) < 0.01

    async def get_frame_status(self) -> dict:
        """Read key frame parameters and report current vehicle frame state.

        Returns detailed status for critical subsystems (lights, relay, battery)
        and reports any mismatches against the frame definition.
        """
        params = await self.fetch_key_params()
        frame = self.load_frame_definition()
        frame_params = frame.get("parameters", {}) if frame else {}
        critical_names = set(frame.get("critical_params", [])) if frame else set()

        orientation_val = params.get("AHRS_ORIENTATION")
        orientation_map = {0: "None", 24: "Pitch90", 25: "Pitch270"}

        servo_functions = {}
        for ch in range(1, 17):
            key = f"SERVO{ch}_FUNCTION"
            val = params.get(key)
            if val is not None:
                servo_functions[key] = int(val)

        active_outputs = {
            k: v for k, v in servo_functions.items() if v != 0
        }

        # Build mismatch report for all fetched critical params
        critical_mismatches = {}
        other_mismatches = {}
        for name in params:
            expected = frame_params.get(name)
            if expected is None:
                continue
            actual = params[name]
            if not self._compare_param(actual, expected):
                entry = {"expected": expected, "actual": actual}
                if name in critical_names:
                    critical_mismatches[name] = entry
                else:
                    other_mismatches[name] = entry

        # Subsystem-specific status
        lights_ok = all(
            self._compare_param(params.get(p, -999), frame_params.get(p, -998))
            for p in ("SERVO13_FUNCTION", "SERVO13_MIN", "SERVO13_MAX", "SERVO13_TRIM")
            if p in frame_params
        )
        relay_ok = all(
            self._compare_param(params.get(p, -999), frame_params.get(p, -998))
            for p in ("SERVO14_FUNCTION", "RELAY1_FUNCTION")
            if p in frame_params
        )
        battery_ok = all(
            self._compare_param(params.get(p, -999), frame_params.get(p, -998))
            for p in ("BATT_CAPACITY", "BATT_MONITOR", "BATT_VOLT_PIN", "BATT_CURR_PIN")
            if p in frame_params
        )

        return {
            "orientation": orientation_map.get(
                int(orientation_val), f"Unknown({int(orientation_val)})"
            ) if orientation_val is not None else None,
            "orientation_raw": int(orientation_val) if orientation_val is not None else None,
            "frame_config": int(params.get("FRAME_CONFIG", -1)),
            "relay_enabled": int(params.get("RELAY1_FUNCTION", 0)) == 1,
            "relay_pin": int(params.get("RELAY1_PIN", -1)),
            "scripting_enabled": int(params.get("SCR_ENABLE", 0)) == 1,
            "active_servo_outputs": active_outputs,
            "lights": {
                "ok": lights_ok,
                "servo13_function": int(params.get("SERVO13_FUNCTION", -1)),
                "expected_function": int(frame_params.get("SERVO13_FUNCTION", -1)),
                "servo13_min": int(params.get("SERVO13_MIN", -1)),
                "servo13_max": int(params.get("SERVO13_MAX", -1)),
                "servo13_trim": int(params.get("SERVO13_TRIM", -1)),
            },
            "relay": {
                "ok": relay_ok,
                "servo14_function": int(params.get("SERVO14_FUNCTION", -1)),
                "expected_function": int(frame_params.get("SERVO14_FUNCTION", -1)),
                "relay1_function": int(params.get("RELAY1_FUNCTION", -1)),
                "relay1_pin": int(params.get("RELAY1_PIN", -1)),
            },
            "battery": {
                "ok": battery_ok,
                "capacity": int(params.get("BATT_CAPACITY", -1)),
                "expected_capacity": int(frame_params.get("BATT_CAPACITY", -1)),
                "monitor": int(params.get("BATT_MONITOR", -1)),
                "volt_pin": int(params.get("BATT_VOLT_PIN", -1)),
                "curr_pin": int(params.get("BATT_CURR_PIN", -1)),
            },
            "frame_applied": len(critical_mismatches) == 0 and len(params) > 0,
            "critical_mismatches": critical_mismatches,
            "other_mismatches": other_mismatches,
        }

    async def apply_frame_if_needed(self, frame_name: str = "doris") -> bool:
        """Apply the frame on first install only.

        Uses a sentinel file in persistent storage to record that the frame
        has been applied.  Once the sentinel exists the frame is never
        re-applied automatically — use the /api/v1/frame/apply endpoint or
        the status check to verify params after that.
        """
        frame = self.load_frame_definition(frame_name)
        if frame is None:
            logger.warning("Cannot check frame: definition '%s' not found", frame_name)
            return False

        frame_version = frame.get("version", 1)

        if FRAME_SENTINEL.exists():
            try:
                sentinel = json.loads(FRAME_SENTINEL.read_text())
                logger.info(
                    "Frame '%s' was applied on %s (v%s), skipping — "
                    "use /api/v1/frame/apply to re-apply manually",
                    frame_name,
                    sentinel.get("applied_at", "?"),
                    sentinel.get("version", "?"),
                )
                return True
            except (json.JSONDecodeError, OSError):
                pass

        for attempt in range(1, STARTUP_MAX_RETRIES + 1):
            logger.info(
                "First install: applying frame '%s' (attempt %d/%d)",
                frame_name,
                attempt,
                STARTUP_MAX_RETRIES,
            )
            result = await self.apply_frame(frame_name)
            if result.get("success"):
                FRAME_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
                FRAME_SENTINEL.write_text(json.dumps({
                    "frame": frame_name,
                    "version": frame_version,
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                    "params_set": result.get("succeeded", 0),
                }))
                logger.info("Frame '%s' applied and sentinel written", frame_name)
                return True

            failed = result.get("failed_params", [])
            logger.warning(
                "Frame apply attempt %d failed (%d params failed: %s), retrying in %.0fs",
                attempt,
                len(failed),
                ", ".join(failed[:5]),
                STARTUP_RETRY_DELAY,
            )
            await asyncio.sleep(STARTUP_RETRY_DELAY)

        logger.error("Frame '%s' failed after %d attempts", frame_name, STARTUP_MAX_RETRIES)
        return False

    async def apply_post_reboot_params(self, frame_name: str = "doris") -> bool:
        """Apply parameters that require a prior reboot (e.g. relay pin assignment).

        ArduPilot's relay driver only initialises on boot.  RELAY1_FUNCTION=1
        enables relay 1 but the driver won't create the relay object until the
        next reboot.  Only after that reboot can RELAY1_PIN be set to assign
        the relay to a physical output channel.

        Each param is set and then read back to confirm the autopilot accepted
        the value.  Returns True only when every param is verified.
        """
        frame = self.load_frame_definition(frame_name)
        if frame is None:
            logger.warning("Cannot apply post-reboot params: frame '%s' not found", frame_name)
            return False

        params = frame.get("post_reboot_parameters", {})
        if not params:
            logger.debug("No post-reboot parameters defined for frame '%s'", frame_name)
            return True

        succeeded = []
        failed = []
        for name, value in params.items():
            ok = await self._set_and_verify_param(name, float(value))
            if ok:
                succeeded.append(name)
            else:
                failed.append(name)
            await asyncio.sleep(PARAM_SET_DELAY)

        if failed:
            logger.warning(
                "Post-reboot params: %d succeeded, %d failed (%s)",
                len(succeeded), len(failed), ", ".join(failed),
            )
        else:
            logger.info(
                "Post-reboot params applied and verified: %s",
                ", ".join(f"{n}={params[n]}" for n in succeeded),
            )

        return len(failed) == 0

    async def _set_param(self, name: str, value: float) -> bool:
        """Send a PARAM_SET message via mavlink2rest."""
        import httpx

        base = _mavlink2rest_http_url()
        payload = {
            "header": {"system_id": 255, "component_id": 0, "sequence": 0},
            "message": {
                "type": "PARAM_SET",
                "target_system": 1,
                "target_component": 1,
                "param_id": list(name.ljust(16, "\x00")),
                "param_value": value,
                "param_type": {"type": "MAV_PARAM_TYPE_REAL32"},
            },
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{base}/mavlink", json=payload)
                resp.raise_for_status()
            logger.debug("Set %s=%.1f", name, value)
            return True
        except Exception as e:
            logger.error("Failed to set %s=%.1f: %s", name, value, e)
            return False

    async def _read_param(self, name: str) -> float | None:
        """Read a parameter value back from the autopilot."""
        import httpx

        base = _mavlink2rest_http_url()
        payload = {
            "header": {"system_id": 255, "component_id": 0, "sequence": 0},
            "message": {
                "type": "PARAM_REQUEST_READ",
                "target_system": 1,
                "target_component": 1,
                "param_id": list(name.ljust(16, "\x00")),
                "param_index": -1,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{base}/mavlink", json=payload)
                resp.raise_for_status()
                await asyncio.sleep(0.5)
                resp = await client.get(
                    f"{base}/mavlink/vehicles/1/components/1/messages/PARAM_VALUE"
                )
                resp.raise_for_status()
                data = resp.json()

            returned_name = "".join(
                c for c in data.get("message", {}).get("param_id", [])
                if c != "\x00"
            )
            if returned_name == name:
                return float(data["message"].get("param_value", 0))
        except Exception as e:
            logger.debug("Failed to read %s: %s", name, e)
        return None

    async def _set_and_verify_param(self, name: str, value: float) -> bool:
        """Set a parameter and read it back to confirm the autopilot accepted it."""
        if not await self._set_param(name, value):
            return False

        actual = await self._read_param(name)
        if actual is not None and int(actual) == int(value):
            logger.debug("Verified %s=%d", name, int(value))
            return True

        logger.warning(
            "Verification failed for %s: expected %d, got %s",
            name, int(value), int(actual) if actual is not None else "no response",
        )
        return False

    async def close(self) -> None:
        """No persistent resources to close."""
