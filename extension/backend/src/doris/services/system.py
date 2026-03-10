"""System information service."""

import logging
from ..config import blueos_services
from ..models.system import SystemStatus, BatteryInfo, StorageInfo, LocationInfo
from .base import BlueOSClient

logger = logging.getLogger(__name__)


class SystemService:
    """Service for getting system information from BlueOS."""

    # Cache for last known good values (to avoid showing mock data on temporary failures)
    _last_storage: StorageInfo | None = None
    _last_location: LocationInfo | None = None

    def __init__(self):
        self.helper = BlueOSClient(blueos_services.helper)
        self.linux2rest = BlueOSClient(blueos_services.linux2rest)
        self.mavlink2rest = BlueOSClient(blueos_services.mavlink2rest)

    async def get_system_status(self) -> SystemStatus:
        """Get complete system status."""
        battery = await self.get_battery_info()
        storage = await self.get_storage_info()

        # Get system metrics from linux2rest
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
        except Exception:
            pass

        return SystemStatus(
            connected=True,
            battery_level=battery.level,
            battery_voltage=battery.voltage or 0.0,
            battery_time_remaining=battery.time_remaining,
            storage_used_percent=storage.used_percent,
            storage_used_gb=storage.used_gb,
            storage_total_gb=storage.total_gb,
            cpu_usage=cpu_percent,
            memory_usage=memory_percent,
            temperature=temperature,
            uptime=uptime,
        )

    async def get_battery_info(self) -> BatteryInfo:
        """Get battery information from MAVLink."""
        try:
            # Try to get battery info from MAVLink2Rest
            battery_data = await self.mavlink2rest.get(
                "/mavlink/vehicles/1/components/1/messages/BATTERY_STATUS"
            )

            # BlueOS returns None if no battery data available
            if battery_data is None:
                raise ValueError("No battery data available")

            message = battery_data.get("message", {})
            if not message:
                raise ValueError("No battery message")

            voltages = message.get("voltages", [0])
            voltage = voltages[0] / 1000.0 if voltages and voltages[0] > 0 else None  # mV to V
            current = message.get("current_battery", 0) / 100.0  # cA to A
            remaining = message.get("battery_remaining", -1)

            # -1 means battery remaining is unknown
            if remaining < 0:
                remaining = 100  # Assume full if unknown

            remaining_hours = self._estimate_remaining_hours(remaining, current)

            return BatteryInfo(
                level=float(remaining),
                voltage=voltage,
                current=current,
                time_remaining=self._format_time_remaining(remaining_hours),
            )
        except Exception:
            # Return mock data if unable to get real data
            return BatteryInfo(
                level=87.0,
                voltage=14.2,
                time_remaining="12.5 hours",
            )

    async def get_storage_info(self) -> StorageInfo:
        """Get storage information."""
        try:
            # BlueOS returns an array of disk partitions
            disk_info = await self.linux2rest.get("/system/disk")

            # Find the main partition (usually the largest or root)
            if isinstance(disk_info, list) and len(disk_info) > 0:
                # Use the first entry (usually overlay/root)
                main_disk = disk_info[0]
                total = main_disk.get("total_space_B", 0)
                available = main_disk.get("available_space_B", 0)

                if total <= 0:
                    logger.warning(f"Invalid disk info: total_space_B={total}")
                    raise ValueError(f"Invalid total disk space: {total}")

                used = total - available

                result = StorageInfo(
                    total_gb=total / (1024**3),
                    used_gb=used / (1024**3),
                    available_gb=available / (1024**3),
                    used_percent=(used / total) * 100,
                )
                # Cache successful result
                SystemService._last_storage = result
                return result

            logger.warning(f"Empty or invalid disk_info response: {disk_info}")
            raise ValueError(f"No disk info available: {disk_info}")
        except Exception as e:
            # Log the error
            logger.warning(f"Failed to get storage info from BlueOS: {type(e).__name__}: {e}")

            # Return cached value if available, otherwise mock data
            if SystemService._last_storage is not None:
                logger.info("Using cached storage info")
                return SystemService._last_storage

            return StorageInfo(
                total_gb=500.0,
                used_gb=225.0,
                available_gb=275.0,
                used_percent=45.0,
            )

    async def get_location(self) -> LocationInfo:
        """Get GPS location from MAVLink."""
        try:
            gps_data = await self.mavlink2rest.get(
                "/mavlink/vehicles/1/components/1/messages/GPS_RAW_INT"
            )
            message = gps_data.get("message", {})

            lat = message.get("lat", 0) / 1e7  # degE7 to degrees
            lon = message.get("lon", 0) / 1e7
            alt = message.get("alt", 0) / 1000.0  # mm to m
            satellites = message.get("satellites_visible", 0)

            # fix_type can be an integer or an object like {"type": "GPS_FIX_TYPE_RTK_FIXED"}
            fix_type_raw = message.get("fix_type", 0)
            if isinstance(fix_type_raw, dict):
                fix_type_str = fix_type_raw.get("type", "")
                # Parse from MAVLink enum name
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

            # Get timestamp from status
            status = gps_data.get("status", {})
            time_info = status.get("time", {})
            last_update_str = time_info.get("last_update", "")

            # Format as relative time
            last_update = "Just now" if last_update_str else "Unknown"

            return LocationInfo(
                latitude=lat,
                longitude=lon,
                altitude=alt,
                fix_type=fix_type,
                satellites=satellites,
                last_update=last_update,
            )
        except Exception:
            # Return mock data
            return LocationInfo(
                latitude=41.7128,
                longitude=-74.0060,
                fix_type="3d",
                satellites=12,
                last_update="2 minutes ago",
            )

    def _estimate_remaining_hours(
        self, percent: float, current: float | None
    ) -> float | None:
        """Estimate remaining battery hours."""
        if current is None or current <= 0:
            # Rough estimate: assume 10A average draw, 100Ah battery
            return (percent / 100) * 10
        # More accurate: based on current draw
        capacity_ah = 100  # Assume 100Ah battery
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

