"""Camera service."""

from ..config import blueos_services
from .base import BlueOSClient


class CameraSettings:
    """Camera configuration."""

    def __init__(
        self,
        resolution: str = "4K",
        frame_rate: int = 30,
        focus: str = "auto",
        brightness: int = 75,
    ):
        self.resolution = resolution
        self.frame_rate = frame_rate
        self.focus = focus
        self.brightness = brightness


class CameraService:
    """Service for managing camera and lighting."""

    def __init__(self):
        self.camera_manager = BlueOSClient(blueos_services.camera_manager)
        self.mavlink2rest = BlueOSClient(blueos_services.mavlink2rest)

    async def get_cameras(self) -> list[dict]:
        """Get list of connected cameras."""
        try:
            cameras = await self.camera_manager.get("/cameras")
            return cameras
        except Exception:
            return []

    async def get_camera_settings(self, camera_id: str = "default") -> CameraSettings:
        """Get camera settings."""
        try:
            settings = await self.camera_manager.get(f"/cameras/{camera_id}/settings")
            return CameraSettings(
                resolution=settings.get("resolution", "4K"),
                frame_rate=settings.get("frame_rate", 30),
                focus=settings.get("focus", "auto"),
                brightness=settings.get("brightness", 75),
            )
        except Exception:
            return CameraSettings()

    async def set_camera_settings(
        self, camera_id: str = "default", settings: CameraSettings = None
    ) -> bool:
        """Update camera settings."""
        if settings is None:
            return False
        try:
            await self.camera_manager.put(
                f"/cameras/{camera_id}/settings",
                json={
                    "resolution": settings.resolution,
                    "frame_rate": settings.frame_rate,
                    "focus": settings.focus,
                    "brightness": settings.brightness,
                },
            )
            return True
        except Exception:
            return False

    async def start_recording(self, camera_id: str = "default") -> bool:
        """Start video recording."""
        try:
            await self.camera_manager.post(f"/cameras/{camera_id}/record/start")
            return True
        except Exception:
            return False

    async def stop_recording(self, camera_id: str = "default") -> bool:
        """Stop video recording."""
        try:
            await self.camera_manager.post(f"/cameras/{camera_id}/record/stop")
            return True
        except Exception:
            return False

    async def take_photo(self, camera_id: str = "default") -> str | None:
        """Take a photo and return the file path."""
        try:
            result = await self.camera_manager.post(f"/cameras/{camera_id}/photo")
            return result.get("path")
        except Exception:
            return None

    async def set_light_brightness(self, brightness: int) -> bool:
        """Set light brightness (0-100)."""
        try:
            # Lights are typically controlled via MAVLink servo/RC override
            # This maps 0-100% to PWM values (typically 1100-1900)
            pwm = 1100 + (brightness / 100) * 800

            await self.mavlink2rest.post(
                "/mavlink",
                json={
                    "header": {"system_id": 255, "component_id": 0},
                    "message": {
                        "type": "RC_CHANNELS_OVERRIDE",
                        "chan9_raw": int(pwm),  # Light channel
                    },
                },
            )
            return True
        except Exception:
            return False

    async def get_stream_url(self, camera_id: str = "default") -> str | None:
        """Get the video stream URL for a camera."""
        try:
            cameras = await self.camera_manager.get("/cameras")
            for cam in cameras:
                if cam.get("id") == camera_id or camera_id == "default":
                    return cam.get("stream_url")
        except Exception:
            pass
        return None

    async def close(self) -> None:
        """Close HTTP clients."""
        await self.camera_manager.close()
        await self.mavlink2rest.close()

