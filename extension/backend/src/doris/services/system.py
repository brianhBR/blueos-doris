"""System information service."""

import logging
import math
import os
from pathlib import Path

import httpx

from ..config import blueos_services
from ..models.system import BatteryInfo, LocationInfo, StorageInfo, SystemStatus
from .base import BlueOSClient
from .external_storage import get_migration_status
from .storage import DATA_ROOT

logger = logging.getLogger(__name__)


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
            remaining = message.get("battery_remaining", -1)

            if remaining < 0 and voltage is not None:
                k = 3.0
                v_mid = 14.52
                soc = 100.0 / (1.0 + math.exp(-k * (voltage - v_mid)))
                remaining = max(0.0, min(100.0, soc))
            elif remaining < 0:
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
        """Estimate remaining battery hours."""
        if current is None or current <= 0:
            capacity_ah = 100
            return (percent / 100) * (capacity_ah / 10)
        capacity_ah = 100
        remaining_ah = (percent / 100) * capacity_ah
        return remaining_ah / current if current > 0 else None

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
