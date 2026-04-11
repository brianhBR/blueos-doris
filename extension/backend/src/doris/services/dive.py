"""Dive control service.

Sets the DORIS_START MAVLink parameter via mavlink2rest to trigger
the onboard Lua dive script.

Before triggering, the service can load a DeploymentConfiguration from
disk and push the relevant settings as DORIS_* parameters so the Lua
state machine uses the correct durations and light settings.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from ..config import blueos_services
from ..models.configuration import (
    CameraSettings,
    CameraType,
    DeploymentConfiguration,
    TimeValue,
)

logger = logging.getLogger(__name__)

PARAM_NAME = "DORIS_START"
STATE_PARAM = "DORIS_STATE"

_DORIS_STATE_NAMES: dict[int, str] = {
    -1: "CONFIG",
    0: "MISSION_START",
    1: "DESCENT",
    2: "ON_BOTTOM",
    3: "ASCENT",
    4: "RECOVERY",
}

DEFAULT_DESCENT_RATE_MPS = 0.5


def _mavlink2rest_url() -> str:
    return blueos_services.mavlink2rest


def _time_value_to_seconds(tv: TimeValue) -> float:
    """Convert a TimeValue (number + unit) to total seconds."""
    try:
        val = float(tv.number) if tv.number else 0.0
    except ValueError:
        return 0.0
    if tv.unit == "minutes":
        return val * 60.0
    if tv.unit == "hours":
        return val * 3600.0
    return val


def _ipcam_phase_enabled(cam: CameraSettings) -> float:
    """Return 1.0 if IP camera recording is wanted for this phase, else 0.0."""
    if cam.enabled and cam.camera_type == CameraType.CONTINUOUS_VIDEO:
        return 1.0
    return 0.0


class DiveService:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._last_known_value: float = 0.0
        self._started_at: float = 0.0

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0, follow_redirects=True)
        return self._client

    async def push_configuration_params(
        self,
        config: DeploymentConfiguration,
        *,
        profile_id: int,
        upload_date: float,
        upload_time: float,
    ) -> bool:
        """Translate a DeploymentConfiguration into DORIS_* ArduPilot parameters."""
        depth_str = config.estimated_depth.strip() if config.estimated_depth else ""
        try:
            depth_m = float(depth_str) if depth_str else 0.0
        except ValueError:
            depth_m = 0.0
        descent_dur_s = max(depth_m / DEFAULT_DESCENT_RATE_MPS, 30.0) if depth_m > 0 else 30.0

        release_seconds = _time_value_to_seconds(config.ascent.release_weight.elapsed)
        release_seconds = max(release_seconds, descent_dur_s + 5)

        btm_light_dly_s = max(0.0, _time_value_to_seconds(config.bottom.light_delay))
        btm_cam_dly_s = max(0.0, _time_value_to_seconds(config.bottom.camera_delay))

        ascent_cam = (
            config.descent.camera if config.ascent.same_as_descent else config.ascent.camera
        )
        d_rec = _ipcam_phase_enabled(config.descent.camera)
        b_rec = _ipcam_phase_enabled(config.bottom.camera)
        a_rec = _ipcam_phase_enabled(ascent_cam)
        rec_master = 1.0 if any(v >= 1.0 for v in (d_rec, b_rec, a_rec)) else 0.0

        params: list[tuple[str, float]] = [
            ("DORIS_DSC_DUR", round(descent_dur_s)),
            ("DORIS_BTM_TIM", round(release_seconds)),
            ("DORIS_DSC_LGT", 1.0 if config.descent.light.enabled else 0.0),
            ("DORIS_BTM_LGT", 1.0 if config.bottom.light.enabled else 0.0),
            ("DORIS_ASC_LGT", 1.0 if config.ascent.light.enabled else 0.0),
            ("DORIS_LGT_BRT", float(config.bottom.light.brightness)),
            ("DORIS_BTM_DLY", round(btm_light_dly_s)),
            ("DORIS_CAM_DLY", round(btm_cam_dly_s)),
            ("DORIS_PRF_ID", float(profile_id)),
            ("DORIS_UPL_DATE", upload_date),
            ("DORIS_UPL_TIME", upload_time),
            ("DORIS_REC_EN", rec_master),
            ("DORIS_DSC_REC", d_rec),
            ("DORIS_BTM_REC", b_rec),
            ("DORIS_ASC_REC", a_rec),
        ]

        all_ok = True
        for name, value in params:
            ok = await self._set_param(name, value)
            if not ok:
                all_ok = False
            await asyncio.sleep(0.05)

        logger.info(
            "Pushed config params: %s",
            ", ".join(f"{n}={v}" for n, v in params),
        )
        return all_ok

    async def start_dive(
        self,
        config: DeploymentConfiguration | None = None,
        *,
        profile_id: int | None = None,
        upload_date: float | None = None,
        upload_time: float | None = None,
    ) -> bool:
        """Push configuration (if given), then set DORIS_START=1."""
        if config is not None:
            now = datetime.now(timezone.utc)
            pid = (
                profile_id
                if profile_id is not None
                else max(int(now.timestamp()) % 2_147_483_647, 1)
            )
            udate = (
                upload_date
                if upload_date is not None
                else float(now.year * 10_000 + now.month * 100 + now.day)
            )
            utime = (
                upload_time
                if upload_time is not None
                else float(now.hour * 100 + now.minute)
            )
            params_ok = await self.push_configuration_params(
                config,
                profile_id=pid,
                upload_date=udate,
                upload_time=utime,
            )
            if not params_ok:
                logger.warning("Some config params failed to set, proceeding anyway")
            await asyncio.sleep(0.2)

        ok = await self._set_param(PARAM_NAME, 1.0)
        if ok:
            self._last_known_value = 1.0
            self._started_at = time.monotonic()
        return ok

    async def set_sim_buoyancy(self, newtons: float) -> bool:
        """Set ArduSub SITL net buoyancy (SIM_BUOYANCY). Negative sinks, positive rises."""
        return await self._set_param("SIM_BUOYANCY", float(newtons))

    async def stop_dive(self) -> bool:
        """Set DORIS_START=0 via PARAM_SET to abort/reset."""
        ok = await self._set_param(PARAM_NAME, 0.0)
        if ok:
            self._last_known_value = 0.0
            self._started_at = 0.0
        return ok

    def _is_active(self, start_val: float, state_int: int | None) -> bool:
        """Determine whether a dive is active.

        DORIS_START is a one-shot trigger that the Lua script clears after
        processing, so reading it back is unreliable.  Instead we check:
        1. DORIS_STATE 0-3 (MISSION_START … ASCENT) — Lua is running.
        2. DORIS_START >= 1 — trigger just set, Lua hasn't consumed it yet.
        3. Cooldown — brief window after start_dive() to cover the race
           between PARAM_SET and the Lua script's first update cycle.
        """
        if state_int is not None and 0 <= state_int <= 3:
            return True
        if start_val >= 1.0:
            return True
        if self._started_at and (time.monotonic() - self._started_at) < 15.0:
            return True
        return False

    def _is_completed(self, state_int: int | None) -> bool:
        """A dive is completed only when the Lua state machine reaches RECOVERY (4)."""
        return state_int is not None and state_int == 4

    async def get_status(self) -> dict:
        """Read DORIS_START and DORIS_STATE from the PARAM_VALUE cache."""
        try:
            start_val = await self._read_param_float(PARAM_NAME)
            if start_val is not None:
                self._last_known_value = start_val

            state_val = await self._read_param_float(STATE_PARAM)
            value = self._last_known_value
            state_int = int(state_val) if state_val is not None else None
            active = self._is_active(value, state_int)
            out: dict = {
                "param": PARAM_NAME,
                "value": value,
                "active": active,
                "completed": self._is_completed(state_int),
                "doris_script_state": state_int,
                "doris_script_state_name": _DORIS_STATE_NAMES.get(state_int, None)
                if state_int is not None
                else None,
            }
            return out
        except Exception as e:
            logger.warning(f"Could not read {PARAM_NAME}: {e}")
            return {
                "param": PARAM_NAME,
                "value": self._last_known_value,
                "active": self._is_active(self._last_known_value, None),
                "completed": False,
                "doris_script_state": None,
                "doris_script_state_name": None,
            }

    async def _read_param_float(self, name: str) -> float | None:
        """Request a parameter and read the latest PARAM_VALUE from mavlink2rest."""
        base = _mavlink2rest_url()
        await self._request_param(name)
        await asyncio.sleep(0.35)

        resp = await self.client.get(
            f"{base}/mavlink/vehicles/1/components/1/messages/PARAM_VALUE"
        )
        resp.raise_for_status()
        data = resp.json()
        message = data.get("message", {})
        param_id = "".join(c for c in message.get("param_id", []) if c != "\x00")
        if param_id != name:
            return None
        return float(message.get("param_value", 0.0))

    async def _request_param(self, name: str) -> None:
        """Send PARAM_REQUEST_READ so the autopilot emits a fresh PARAM_VALUE."""
        base = _mavlink2rest_url()
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
            resp = await self.client.post(f"{base}/mavlink", json=payload)
            resp.raise_for_status()
        except Exception as e:
            logger.debug(f"PARAM_REQUEST_READ for {name} failed: {e}")

    async def _set_param(self, name: str, value: float) -> bool:
        """Send a PARAM_SET message via mavlink2rest."""
        base = _mavlink2rest_url()
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
            resp = await self.client.post(f"{base}/mavlink", json=payload)
            resp.raise_for_status()
            logger.info(f"Set {name}={value} (status {resp.status_code})")
            return True
        except Exception as e:
            logger.error(f"Failed to set {name}={value}: {e}")
            return False

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
