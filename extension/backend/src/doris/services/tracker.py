"""Artemis Global Tracker detection and GPS data service.

Detects the SparkFun Artemis Global Tracker by checking for its
HEARTBEAT on MAVLink component 191 (MAV_COMP_ID_ONBOARD_COMPUTER).
Reads GPS_RAW_INT from the same component for position data.
"""

import logging
from datetime import datetime

import httpx

from ..config import blueos_services
from ..models.sensors import ModuleInfo

logger = logging.getLogger(__name__)

ARTEMIS_COMPONENT_ID = 191
GPS_FIX_NAMES = {0: "No GPS", 1: "No Fix", 2: "2D Fix", 3: "3D Fix", 4: "DGPS", 5: "RTK Float", 6: "RTK Fixed"}


def _m2r_base() -> str:
    return blueos_services.mavlink2rest


class ArtemisTrackerService:
    """Detects the Artemis tracker and reads its GPS data."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=5.0, follow_redirects=True)
        return self._client

    async def get_modules(self) -> list[ModuleInfo]:
        """Return a ModuleInfo for the tracker if a recent heartbeat exists."""
        hb = await self._get_heartbeat()
        if hb is None:
            return []

        gps = await self.get_gps_data()

        if gps and gps.get("fix_type", 0) >= 2:
            status_text = (
                f"{gps['fix_type_name']} | "
                f"{gps['lat']:.6f}, {gps['lon']:.6f} | "
                f"{gps['satellites']} sats"
            )
        elif gps:
            status_text = f"{gps['fix_type_name']} | {gps['satellites']} sats"
        else:
            status_text = "Connected (no GPS data)"

        return [
            ModuleInfo(
                id="artemis-tracker",
                name="Artemis Global Tracker",
                type="tracker",
                status="connected",
                module_status=status_text,
                last_reading=datetime.now().isoformat(),
            )
        ]

    async def get_gps_data(self) -> dict | None:
        """Read the latest GPS_RAW_INT from the Artemis (component 191)."""
        base = _m2r_base()
        url = f"{base}/mavlink/vehicles/1/components/{ARTEMIS_COMPONENT_ID}/messages/GPS_RAW_INT"
        try:
            resp = await self.client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            msg = data.get("message", {})
            if msg.get("type") != "GPS_RAW_INT":
                return None

            fix_type = msg.get("fix_type", 0)
            if isinstance(fix_type, dict):
                fix_type = fix_type.get("type", 0)
                try:
                    fix_type = int(str(fix_type).split("_")[-1])
                except (ValueError, IndexError):
                    fix_type = 0

            lat = msg.get("lat", 0) / 1e7
            lon = msg.get("lon", 0) / 1e7
            alt = msg.get("alt", 0) / 1000.0
            satellites = msg.get("satellites_visible", 0)
            hdop = msg.get("eph", 65535)
            if hdop != 65535:
                hdop = hdop / 100.0
            else:
                hdop = None
            speed = msg.get("vel", 0) / 100.0
            course = msg.get("cog", 0) / 100.0

            time_info = data.get("status", {}).get("time", {})
            last_update = time_info.get("last_update")

            return {
                "fix_type": fix_type,
                "fix_type_name": GPS_FIX_NAMES.get(fix_type, f"Unknown ({fix_type})"),
                "lat": lat,
                "lon": lon,
                "alt_m": alt,
                "satellites": satellites,
                "hdop": hdop,
                "speed_mps": speed,
                "course_deg": course,
                "last_update": last_update,
            }
        except httpx.HTTPStatusError:
            return None
        except Exception as e:
            logger.debug("Failed to read Artemis GPS: %s", e)
            return None

    async def _get_heartbeat(self) -> dict | None:
        """Check for a recent HEARTBEAT from the Artemis component."""
        base = _m2r_base()
        url = f"{base}/mavlink/vehicles/1/components/{ARTEMIS_COMPONENT_ID}/messages/HEARTBEAT"
        try:
            resp = await self.client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            time_info = data.get("status", {}).get("time", {})
            freq = time_info.get("frequency", 0)
            if freq < 0.05:
                return None
            return data.get("message")
        except Exception as e:
            logger.debug("No Artemis heartbeat: %s", e)
            return None

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
