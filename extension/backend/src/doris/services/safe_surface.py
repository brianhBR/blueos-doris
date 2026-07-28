"""Safe-surface release status and guarded AGT shutdown protocol.

The protocol uses MAVLink ``NAMED_VALUE_FLOAT`` messages:

* autopilot component 1 publishes ``RELAY`` as the requested release state;
* AGT component 192 publishes ``AGT_CAP=3``, ``REL_STAT`` and ``PWR_SHDN``;
* ``PWR_SHDN=1`` requests shutdown and ``PWR_SHDN=0`` resets the handshake.
  BlueOS flushes recording and storage, publishes ``PWR_ACK=1``, then asks BlueOS
  Commander to power off the host.

Lua mirrors every release request to the Navigator relay and to the AGT, so a
mission only needs one of the two release paths to be healthy.  Cutting host
power removes the Navigator path entirely, so the shutdown handshake is
disabled by default and must only be enabled when the actuator is wired to the
AGT.  STATUSTEXT is deliberately not accepted as a control input.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import websockets

from ..config import blueos_services, settings

logger = logging.getLogger(__name__)

AUTOPILOT_COMPONENT_ID = 1
# doris.lua's STATE_RECOVERY, the terminal mission state.
LUA_STATE_RECOVERY = 4
AGT_COMPONENT_ID = 192
CAP_AGT_RELEASE_OWNER = 1 << 0
CAP_SAFE_SHUTDOWN = 1 << 1
REQUIRED_CAPABILITIES = CAP_AGT_RELEASE_OWNER | CAP_SAFE_SHUTDOWN

STATUS_STALE_S = 5.0
RECONNECT_DELAY_S = 2.0
BINARY_VALUE_TOLERANCE = 0.1
MAX_CAPABILITY_MASK = 0xFF


def _decode_name(raw_name: object) -> str:
    """Decode MAVLink's ten-character name field."""
    if isinstance(raw_name, list):
        return "".join(str(char) for char in raw_name if char != "\x00").strip()
    return str(raw_name or "").replace("\x00", "").strip()


def parse_named_value(raw: str) -> tuple[int, int, str, float] | None:
    """Return ``(system, component, name, value)`` for a named float."""
    if not isinstance(raw, str) or not raw.startswith("{"):
        return None
    try:
        data = json.loads(raw)
        message = data.get("message", {})
        header = data.get("header", {})
        if message.get("type") != "NAMED_VALUE_FLOAT":
            return None
        value = float(message.get("value"))
        if not math.isfinite(value):
            return None
        return (
            int(header.get("system_id", -1)),
            int(header.get("component_id", -1)),
            _decode_name(message.get("name")),
            value,
        )
    except (
        AttributeError,
        OverflowError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None


def _named_float_payload(name: str, value: float) -> dict:
    """Build a BlueOS-to-AGT named float with the required source identity."""
    return {
        "header": {"system_id": 1, "component_id": 191, "sequence": 0},
        "message": {
            "type": "NAMED_VALUE_FLOAT",
            "time_boot_ms": 0,
            "name": list(name.ljust(10, "\x00")),
            "value": value,
        },
    }


def _binary_value(value: object) -> bool | None:
    """Decode a finite 0/1 value using the AGT's inclusive ±0.1 tolerance."""
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    if -BINARY_VALUE_TOLERANCE <= numeric <= BINARY_VALUE_TOLERANCE:
        return False
    if (
        1.0 - BINARY_VALUE_TOLERANCE
        <= numeric
        <= 1.0 + BINARY_VALUE_TOLERANCE
    ):
        return True
    return None


def _agt_release_reasons(protocol: dict, firmware: dict) -> list[str]:
    """Explain why the AGT release path cannot be relied on."""
    reasons = []
    if not firmware.get("known"):
        reasons.append("AGT firmware version is unknown")
    elif firmware.get("compatible") is not True:
        reasons.append(
            f"AGT firmware {firmware.get('version')} is older than "
            f"{firmware.get('min_required')}"
        )
    if not protocol["capabilities_known"]:
        reasons.append("AGT capability advertisement is unknown")
    elif not protocol["release_capability_ok"]:
        reasons.append("AGT does not advertise the release capability")
    if not protocol["release_request_known"]:
        reasons.append("Lua release request is missing or stale")
    if not protocol["release_actual_known"]:
        reasons.append("AGT release status is missing or stale")
    if protocol["release_mismatch"]:
        reasons.append("Lua release request and AGT release status disagree")
    if not reasons and not protocol["agt_release_path_ok"]:
        reasons.append("AGT release path is unavailable")
    return reasons


def _capability_mask(value: object) -> int | None:
    """Decode a small finite nonnegative integer capability mask."""
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    rounded = round(numeric)
    if (
        numeric != rounded
        or rounded < 0
        or rounded > MAX_CAPABILITY_MASK
    ):
        return None
    return int(rounded)


@dataclass
class SafeSurfaceState:
    """Last observed safe-surface protocol state."""

    firmware_compatible: bool | None = None
    capabilities: int | None = None
    release_requested: bool | None = None
    release_actual: bool | None = None
    power_shutdown_requested: bool = False
    last_capability_update_monotonic: float | None = None
    last_release_actual_update_monotonic: float | None = None
    last_release_request_monotonic: float | None = None
    last_release_request: str | None = None
    last_agt_update_monotonic: float | None = None
    last_agt_update: str | None = None
    shutdown_state: str = "disabled"
    shutdown_error: str | None = None
    # Latched once Lua reports RECOVERY, so a late UI poll can tell a finished
    # dive apart from an abandoned one even after the vehicle has restarted.
    recovery_seen: bool = False


class SafeSurfaceService:
    """Track the AGT protocol and execute an opt-in shutdown handshake."""

    def __init__(self) -> None:
        self.state = SafeSurfaceState(
            shutdown_state="idle" if settings.agt_shutdown_enabled else "disabled"
        )
        self._task: asyncio.Task | None = None
        self._shutdown_task: asyncio.Task | None = None
        self._shutdown_request_latched = False
        self._last_logged_mismatch: bool | None = None

    def start(self) -> None:
        """Start the MAVLink subscriber once."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._subscriber_loop(), name="safe-surface-mavlink"
            )

    async def _subscriber_loop(self) -> None:
        base = blueos_services.mavlink2rest
        ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
        url = f"{ws_base}/ws/mavlink?filter=NAMED_VALUE_FLOAT"
        while True:
            try:
                async with websockets.connect(url, close_timeout=5) as websocket:
                    async for raw in websocket:
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="ignore")
                        parsed = parse_named_value(raw)
                        if parsed is not None:
                            try:
                                self.process_named_value(*parsed)
                            except Exception:
                                logger.exception(
                                    "Ignored malformed safe-surface message"
                                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.debug(
                    "Safe-surface MAVLink subscriber disconnected (%s)", error
                )
                await asyncio.sleep(RECONNECT_DELAY_S)

    def process_named_value(
        self, system_id: int, component_id: int, name: str, value: float
    ) -> None:
        """Apply one validated protocol message."""
        if system_id != 1:
            return
        if component_id == AUTOPILOT_COMPONENT_ID and name == "RELAY":
            requested = _binary_value(value)
            if requested is None:
                return
            self.state.release_requested = requested
            self.state.last_release_request_monotonic = time.monotonic()
            self.state.last_release_request = datetime.now(timezone.utc).isoformat()
            self._log_release_mismatch()
            return
        if component_id == AUTOPILOT_COMPONENT_ID and name == "STATE":
            if abs(value - LUA_STATE_RECOVERY) <= 0.1:
                self.state.recovery_seen = True
            return
        if component_id != AGT_COMPONENT_ID:
            return

        if name == "AGT_CAP":
            capabilities = _capability_mask(value)
            if capabilities is None:
                return
            self.state.capabilities = capabilities
            self.state.last_capability_update_monotonic = time.monotonic()
        elif name == "REL_STAT":
            actual = _binary_value(value)
            if actual is None:
                return
            self.state.release_actual = actual
            self.state.last_release_actual_update_monotonic = time.monotonic()
            self._log_release_mismatch()
        elif name == "PWR_SHDN":
            requested = _binary_value(value)
            if requested is None:
                return
            self._handle_shutdown_request(requested)
        else:
            return

        self.state.last_agt_update_monotonic = time.monotonic()
        self.state.last_agt_update = datetime.now(timezone.utc).isoformat()

    def _log_release_mismatch(self) -> None:
        """Log only release mismatch transitions to avoid 2 Hz log spam."""
        requested = self.state.release_requested
        actual = self.state.release_actual
        if requested is None or actual is None:
            return
        mismatch = requested != actual
        if mismatch == self._last_logged_mismatch:
            return
        self._last_logged_mismatch = mismatch
        if mismatch:
            logger.error(
                "AGT release mismatch: requested=%s actual=%s", requested, actual
            )
        else:
            logger.info("AGT release request and physical state agree: %s", actual)

    def _handle_shutdown_request(self, requested: bool) -> None:
        """Schedule one shutdown per asserted AGT request."""
        self.state.power_shutdown_requested = requested
        if not requested:
            self._shutdown_request_latched = False
            if self._shutdown_task is None or self._shutdown_task.done():
                self._shutdown_task = None
                self.state.shutdown_error = None
                self.state.shutdown_state = (
                    "idle" if settings.agt_shutdown_enabled else "disabled"
                )
            return
        if self._shutdown_request_latched:
            return
        if not settings.agt_shutdown_enabled:
            self._shutdown_request_latched = True
            self.state.shutdown_state = "disabled"
            logger.warning("Ignored AGT shutdown request: feature is disabled")
            return
        capabilities = self.state.capabilities or 0
        if self.state.firmware_compatible is not True:
            self.state.shutdown_error = "AGT firmware compatibility is not verified"
            return
        capability_updated = self.state.last_capability_update_monotonic
        capability_stale = (
            capability_updated is None
            or max(0.0, time.monotonic() - capability_updated) > STATUS_STALE_S
        )
        if capability_stale:
            self.state.shutdown_error = "AGT capability advertisement is stale"
            return
        if not capabilities & CAP_SAFE_SHUTDOWN:
            self.state.shutdown_error = "AGT safe-shutdown capability is missing"
            return
        if self._shutdown_task is not None and not self._shutdown_task.done():
            return
        self._shutdown_request_latched = True
        self._shutdown_task = asyncio.create_task(
            self._shutdown_sequence(), name="agt-safe-shutdown"
        )

    async def _shutdown_sequence(self) -> None:
        """Flush data, acknowledge the AGT, then request host poweroff."""
        self.state.shutdown_state = "flushing"
        try:
            await self._flush_and_finalize()
            if not await self._run_host_command("sync"):
                raise RuntimeError("host sync failed")

            self.state.shutdown_state = "acknowledging"
            if not await self._send_named_float("PWR_ACK", 1.0):
                raise RuntimeError("MAVLink shutdown acknowledgement failed")

            self.state.shutdown_state = "poweroff_requested"
            if not await self._run_host_command("sudo systemctl poweroff"):
                raise RuntimeError("Commander rejected host poweroff")
        except Exception as error:
            self.state.shutdown_state = "error"
            self.state.shutdown_error = str(error)
            self._shutdown_request_latched = False
            logger.exception("AGT safe shutdown failed")

    def recovery_seen(self) -> bool:
        """True once Lua has reported RECOVERY on this run."""
        return self.state.recovery_seen

    async def _flush_and_finalize(self) -> None:
        """Quiesce the dive before acknowledging the AGT's shutdown request.

        Only cheap work belongs here.  The AGT holds payload power up while it
        waits for the acknowledgement, so running ffmpeg or USB copies at this
        point would burn the surface battery we are trying to save and risk
        being killed mid-write.  Video and exports are deferred to the
        operator-triggered processing job.
        """
        from .dive_processing import quiesce_dive

        await quiesce_dive()

    async def _send_named_float(self, name: str, value: float) -> bool:
        payload = _named_float_payload(name, value)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    f"{blueos_services.mavlink2rest}/mavlink", json=payload
                )
                response.raise_for_status()
            logger.info("Sent AGT shutdown acknowledgement before host poweroff")
            return True
        except Exception as error:
            logger.warning("Failed to send %s: %s", name, error)
            return False

    async def _run_host_command(self, command: str) -> bool:
        url = f"{blueos_services.commander}/v1.0/command/host"
        params = {"command": command, "i_know_what_i_am_doing": "true"}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, params=params)
                response.raise_for_status()
                return int(response.json().get("return_code", -1)) == 0
        except Exception as error:
            logger.warning("Commander command failed (%s): %s", command, error)
            return False

    def status(self, firmware: dict | None = None) -> dict:
        """Return compatibility and release mismatch status."""
        firmware = firmware or {}
        if "compatible" in firmware:
            self.state.firmware_compatible = firmware.get("compatible")
        capabilities = self.state.capabilities
        capability_ok = (
            capabilities is not None
            and capabilities & REQUIRED_CAPABILITIES == REQUIRED_CAPABILITIES
        )
        firmware_ok = self.state.firmware_compatible is True
        compatible = capability_ok and firmware_ok
        now = time.monotonic()

        def stale(timestamp: float | None) -> bool:
            return timestamp is None or max(0.0, now - timestamp) > STATUS_STALE_S

        capability_stale = stale(self.state.last_capability_update_monotonic)
        release_actual_stale = stale(
            self.state.last_release_actual_update_monotonic
        )
        release_request_stale = stale(self.state.last_release_request_monotonic)
        release_request_known = (
            self.state.release_requested is not None and not release_request_stale
        )
        release_actual_known = (
            self.state.release_actual is not None and not release_actual_stale
        )
        mismatch = (
            release_request_known
            and release_actual_known
            and self.state.release_requested != self.state.release_actual
        )
        release_capability_ok = (
            capabilities is not None
            and bool(capabilities & CAP_AGT_RELEASE_OWNER)
        )
        # The AGT release path needs a firmware that advertises the capability
        # plus a live request/status pair that agrees.
        agt_release_path_ok = (
            firmware_ok
            and release_capability_ok
            and not capability_stale
            and release_request_known
            and release_actual_known
            and not mismatch
        )
        return {
            "capabilities": capabilities,
            "required_capabilities": REQUIRED_CAPABILITIES,
            "capabilities_known": capabilities is not None,
            "capabilities_compatible": capability_ok,
            "compatible": compatible,
            "release_capability_ok": release_capability_ok,
            "agt_release_path_ok": agt_release_path_ok,
            "release_request_known": release_request_known,
            "release_request_stale": release_request_stale,
            "release_actual_known": release_actual_known,
            "release_actual_stale": release_actual_stale,
            "release_requested": self.state.release_requested,
            "release_actual": self.state.release_actual,
            "release_mismatch": mismatch,
            "agt_status_stale": capability_stale or release_actual_stale,
            "last_agt_update": self.state.last_agt_update,
            "shutdown_enabled": settings.agt_shutdown_enabled,
            "power_shutdown_requested": self.state.power_shutdown_requested,
            "shutdown_state": self.state.shutdown_state,
            "shutdown_error": self.state.shutdown_error,
        }

    def evaluate_release_readiness(self, firmware: dict, frame: dict) -> dict:
        """Judge the two mirrored release paths for a mission start decision.

        A mission needs only one healthy path, so a degraded path is a warning
        while losing both is a blocker.  When the AGT is allowed to power off
        the host the Navigator path disappears mid-recovery, so that
        configuration additionally demands a healthy AGT path.
        """
        protocol = self.status(firmware)
        navigator_ok = bool(
            frame.get("frame_applied") and frame.get("relay", {}).get("ok")
        )
        agt_ok = bool(protocol["agt_release_path_ok"])
        agt_reasons = _agt_release_reasons(protocol, firmware)
        navigator_reasons = [] if navigator_ok else [
            "Navigator release output is unavailable: RELAY1_FUNCTION, "
            "RELAY1_PIN, or SERVO14_FUNCTION does not match the frame"
        ]

        blockers: list[str] = []
        warnings: list[str] = []
        if navigator_ok or agt_ok:
            warnings.extend(navigator_reasons)
            warnings.extend(agt_reasons)
        else:
            blockers.append("Neither the Navigator nor the AGT release path is available")
            blockers.extend(navigator_reasons)
            blockers.extend(agt_reasons)
        if settings.agt_shutdown_enabled and not agt_ok:
            blockers.append(
                "AGT host shutdown is enabled, which requires a healthy AGT "
                "release path because host power loss disables the Navigator output"
            )
            blockers.extend(r for r in agt_reasons if r not in blockers)

        return {
            "ready": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "navigator_release_available": navigator_ok,
            "agt_release_available": agt_ok,
            "protocol": protocol,
        }

    async def close(self) -> None:
        """Stop background protocol tasks."""
        for task in (self._task, self._shutdown_task):
            if task is not None and not task.done():
                task.cancel()
        self._task = None
        self._shutdown_task = None


safe_surface_service = SafeSurfaceService()
