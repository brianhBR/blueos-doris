"""System information service."""

import logging
import os
from pathlib import Path

import httpx

from ..config import blueos_services
from ..models.system import BatteryInfo, LocationInfo, StorageInfo, SystemStatus
from . import power_model
from .base import BlueOSClient
from .external_storage import get_migration_status
from .storage import DATA_ROOT

logger = logging.getLogger(__name__)

# 4S LiPo pack voltage → State-of-Charge (%), piecewise-linear.
# Derived from a typical resting-cell discharge curve (×4 cells in series).
# Used because ArduPilot's BATTERY_STATUS.battery_remaining (coulomb-counted
# against BATT_CAPACITY) defaults near 100% and is unreliable on this rig.
_SOC_CURVE_4S: list[tuple[float, float]] = [
    (16.80, 100.0),
    (16.45, 90.0),
    (16.08, 80.0),
    (15.80, 70.0),
    (15.48, 60.0),
    (15.36, 50.0),
    (15.20, 40.0),
    (15.08, 30.0),
    (14.92, 20.0),
    (14.76, 10.0),
    (14.44, 5.0),
    (13.20, 0.0),
]

def _voltage_to_soc_4s(voltage: float) -> float:
    """Map a measured 4S pack voltage to State-of-Charge (0–100 %).

    Linearly interpolates between points on `_SOC_CURVE_4S`. Voltages above
    or below the curve are clamped to 100 % / 0 %.
    """
    curve = _SOC_CURVE_4S
    if voltage >= curve[0][0]:
        return curve[0][1]
    if voltage <= curve[-1][0]:
        return curve[-1][1]
    for (v_hi, soc_hi), (v_lo, soc_lo) in zip(curve, curve[1:]):
        if v_lo <= voltage <= v_hi:
            span = v_hi - v_lo
            if span <= 0:
                return soc_lo
            frac = (voltage - v_lo) / span
            return soc_lo + frac * (soc_hi - soc_lo)
    return 0.0


class SystemService:
    """Service for getting system information from BlueOS."""

    _last_storage: StorageInfo | None = None
    _last_battery: BatteryInfo | None = None
    _last_location: LocationInfo | None = None

    def __init__(self):
        self.helper = BlueOSClient(blueos_services.helper)
        self.linux2rest = BlueOSClient(blueos_services.linux2rest)
        self.mavlink2rest = BlueOSClient(blueos_services.mavlink2rest)

    async def get_system_status(self) -> SystemStatus:
        """Get complete system status.

        Aggregates battery, storage, and system metrics.
        Individual subsystem failures are logged and surfaced as
        unavailable values rather than fake data.
        """
        battery: BatteryInfo | None = None
        storage: StorageInfo | None = None

        try:
            battery = await self.get_battery_info()
        except Exception as e:
            logger.warning(f"Battery info unavailable: {e}")

        try:
            storage = await self.get_storage_info()
        except Exception as e:
            logger.warning(f"Storage info unavailable: {e}")

        cpu_percent = 0.0
        memory_percent = 0.0
        temperature = None
        uptime = "0:00:00"

        try:
            system_info = await self.linux2rest.get("/system/info")
            cpu_percent = system_info.get("cpu_percent", 0.0)
            memory_percent = system_info.get("memory_percent", 0.0)
            temperature = system_info.get("temperature")
            uptime_secs = system_info.get("uptime", 0)
            hours, remainder = divmod(int(uptime_secs), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime = f"{hours}:{minutes:02d}:{seconds:02d}"
        except Exception as e:
            logger.warning(f"System info unavailable: {e}")

        return SystemStatus(
            connected=True,
            battery_level=battery.level if battery else 0.0,
            battery_voltage=battery.voltage or 0.0 if battery else 0.0,
            battery_time_remaining=battery.time_remaining if battery else "Unavailable",
            storage_used_percent=storage.used_percent if storage else 0.0,
            storage_used_gb=storage.used_gb if storage else 0.0,
            storage_total_gb=storage.total_gb if storage else 0.0,
            cpu_usage=cpu_percent,
            memory_usage=memory_percent,
            temperature=temperature,
            uptime=uptime,
        )

    async def get_battery_info(self) -> BatteryInfo:
        """Get battery information from MAVLink.

        Raises on failure so the caller (route or get_system_status)
        can decide how to handle it.
        """
        try:
            battery_data = await self.mavlink2rest.get(
                "/mavlink/vehicles/1/components/1/messages/BATTERY_STATUS"
            )

            if battery_data is None:
                raise ValueError("No battery data available from MAVLink")

            message = battery_data.get("message", {})
            if not message:
                raise ValueError("Empty BATTERY_STATUS message")

            voltages = message.get("voltages", [0])
            voltage = voltages[0] / 1000.0 if voltages and voltages[0] > 0 else None
            current = message.get("current_battery", 0) / 100.0

            # Always derive SOC from the measured pack voltage. ArduPilot's
            # battery_remaining field is a coulomb-counted estimate that
            # defaults to ~99 % until BATT_CAPACITY worth of mAh have been
            # consumed, so it misreports SOC on a partially-charged pack.
            if voltage is not None:
                remaining = _voltage_to_soc_4s(voltage)
            else:
                remaining = 0.0

            remaining_hours = self._estimate_remaining_hours(remaining, current)

            result = BatteryInfo(
                level=float(remaining),
                voltage=voltage,
                current=current,
                time_remaining=self._format_time_remaining(remaining_hours),
            )
            SystemService._last_battery = result
            return result
        except Exception as e:
            logger.warning(f"Failed to get battery info: {type(e).__name__}: {e}")
            if SystemService._last_battery is not None:
                logger.info("Using cached battery info")
                return SystemService._last_battery
            raise

    async def get_storage_info(self) -> StorageInfo:
        """Get storage info for the filesystem holding dive/recorder data.

        When external storage migration is done, queries the host via
        Commander for the actual USB drive stats — the container's bind
        mount was resolved at creation time and may still point at the
        SD card filesystem.  Falls back to os.statvfs inside the
        container when Commander is unavailable.
        """
        try:
            migration = get_migration_status()
            is_external = migration.get("state") == "done"

            host_stats = None
            if is_external:
                host_stats = await self._get_host_storage_stats("/mnt")

            if host_stats:
                total, available = host_stats
            else:
                recorder_path = DATA_ROOT / "recorder"
                stat_path = recorder_path if recorder_path.exists() else DATA_ROOT
                vfs = os.statvfs(str(stat_path))
                total = vfs.f_frsize * vfs.f_blocks
                available = vfs.f_frsize * vfs.f_bavail

            if total <= 0:
                raise ValueError(f"Invalid total disk space: {total}")
            used = total - available

            result = StorageInfo(
                total_gb=total / (1024**3),
                used_gb=used / (1024**3),
                available_gb=available / (1024**3),
                used_percent=(used / total) * 100,
                storage_type="External USB" if is_external else "SD Card",
            )
            SystemService._last_storage = result
            return result
        except Exception as e:
            logger.warning(f"Failed to get storage info: {type(e).__name__}: {e}")
            if SystemService._last_storage is not None:
                logger.info("Using cached storage info")
                return SystemService._last_storage
            raise

    async def _get_host_storage_stats(self, mount_point: str) -> tuple[int, int] | None:
        """Query the host for filesystem stats via Commander.

        Returns (total_bytes, available_bytes) or None on failure.
        """
        url = f"{blueos_services.commander}/v1.0/command/host"
        cmd = f"df -B1 --output=size,avail {mount_point} | tail -1"
        params = {"command": cmd, "i_know_what_i_am_doing": "true"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                if data.get("return_code", -1) != 0:
                    return None
                stdout = data.get("stdout", "").strip("'\"").replace("\\n", "\n").strip()
                parts = stdout.split()
                if len(parts) >= 2:
                    return int(parts[0]), int(parts[1])
        except Exception as e:
            logger.debug("Host storage stats unavailable: %s", e)
        return None

    async def get_location(self) -> LocationInfo:
        """Get GPS location from MAVLink.

        Raises on failure if no cached data is available.
        """
        try:
            gps_data = await self.mavlink2rest.get(
                "/mavlink/vehicles/1/components/1/messages/GPS_RAW_INT"
            )
            message = gps_data.get("message", {})

            lat = message.get("lat", 0) / 1e7
            lon = message.get("lon", 0) / 1e7
            alt = message.get("alt", 0) / 1000.0
            satellites = message.get("satellites_visible", 0)

            fix_type_raw = message.get("fix_type", 0)
            if isinstance(fix_type_raw, dict):
                fix_type_str = fix_type_raw.get("type", "")
                fix_type_map = {
                    "GPS_FIX_TYPE_NO_GPS": "none",
                    "GPS_FIX_TYPE_NO_FIX": "no_fix",
                    "GPS_FIX_TYPE_2D_FIX": "2d",
                    "GPS_FIX_TYPE_3D_FIX": "3d",
                    "GPS_FIX_TYPE_DGPS": "dgps",
                    "GPS_FIX_TYPE_RTK_FLOAT": "rtk_float",
                    "GPS_FIX_TYPE_RTK_FIXED": "rtk_fixed",
                    "GPS_FIX_TYPE_STATIC": "static",
                    "GPS_FIX_TYPE_PPP": "ppp",
                }
                fix_type = fix_type_map.get(fix_type_str, "unknown")
            else:
                fix_type_names = {
                    0: "none",
                    1: "no_fix",
                    2: "2d",
                    3: "3d",
                    4: "dgps",
                    5: "rtk_float",
                    6: "rtk_fixed",
                }
                fix_type = fix_type_names.get(fix_type_raw, "unknown")

            status = gps_data.get("status", {})
            time_info = status.get("time", {})
            last_update_str = time_info.get("last_update", "")
            last_update = "Just now" if last_update_str else "Unknown"

            result = LocationInfo(
                latitude=lat,
                longitude=lon,
                altitude=alt,
                fix_type=fix_type,
                satellites=satellites,
                last_update=last_update,
            )
            SystemService._last_location = result
            return result
        except Exception as e:
            logger.warning(f"Failed to get location: {type(e).__name__}: {e}")
            if SystemService._last_location is not None:
                logger.info("Using cached location info")
                return SystemService._last_location
            raise

    def _estimate_remaining_hours(
        self, percent: float, current: float | None
    ) -> float | None:
        """Estimate remaining battery hours before hitting the safety reserve.

        Uses the shared :mod:`power_model` (2× 10 Ah 4S packs, 15% reserve).
        With a measured current draw the remaining usable ampere-hours are
        divided by the instantaneous current; otherwise we fall back to the
        empirically-measured typical dive load (~11 W whole-dive average).
        """
        usable_pct = max(0.0, percent - power_model.BATTERY_RESERVE_PCT)
        usable_ah = (usable_pct / 100.0) * power_model.BATTERY_TOTAL_AH
        if current is None or current <= 0:
            fallback_current_a = (
                power_model.TYPICAL_DIVE_LOAD_W / power_model.BATTERY_NOMINAL_V
            )
            return usable_ah / fallback_current_a
        return usable_ah / current

    def _format_time_remaining(self, hours: float | None) -> str:
        """Format remaining hours as a human-readable string."""
        if hours is None:
            return "Unknown"
        if hours < 1:
            minutes = int(hours * 60)
            return f"{minutes} minutes"
        return f"{hours:.1f} hours"

    async def close(self) -> None:
        """Close all HTTP clients."""
        await self.helper.close()
        await self.linux2rest.close()
        await self.mavlink2rest.close()
