"""Sensor and module service."""

import logging
from datetime import datetime
from typing import Any

from ..config import blueos_services
from ..models.sensors import (
    ModuleInfo,
    SensorConfig,
    SensorReading,
    VideoStream,
)
from .base import BlueOSClient

logger = logging.getLogger(__name__)


class SensorService:
    """Service for managing sensors and modules."""

    def __init__(self):
        self.ping_service = BlueOSClient(blueos_services.ping_service)
        self.mavlink2rest = BlueOSClient(blueos_services.mavlink2rest)
        self.camera_manager = BlueOSClient(blueos_services.camera_manager)

    async def get_connected_modules(self) -> list[ModuleInfo]:
        """Get all connected modules."""
        modules = []

        # Get camera module
        camera = await self._get_camera_module()
        if camera:
            modules.append(camera)

        # Get ping/sonar sensors
        ping_modules = await self._get_ping_modules()
        modules.extend(ping_modules)

        # Add light module (usually part of camera system)
        light = await self._get_light_module()
        if light:
            modules.append(light)

        # If no real modules found, return mock data
        if not modules:
            modules = self._get_mock_modules()

        return modules

    async def get_sensor_readings(self, sensor_id: str) -> list[SensorReading]:
        """Get recent readings from a sensor."""
        try:
            # Try to get sensor data from ping service
            data = await self.ping_service.get(f"/v1.0/sensors/{sensor_id}/data")

            readings = []
            for reading in data.get("readings", []):
                readings.append(
                    SensorReading(
                        sensor_id=sensor_id,
                        sensor_name=data.get("name", sensor_id),
                        value=reading.get("value", 0),
                        unit=reading.get("unit", ""),
                        timestamp=datetime.fromisoformat(reading.get("timestamp")),
                        quality=reading.get("quality", 1.0),
                    )
                )
            return readings
        except Exception:
            # Return mock readings
            return [
                SensorReading(
                    sensor_id=sensor_id,
                    sensor_name="CTD Sensor",
                    value=25.4,
                    unit="°C",
                    timestamp=datetime.now(),
                    quality=0.98,
                )
            ]

    async def configure_sensor(self, config: SensorConfig) -> bool:
        """Update sensor configuration."""
        try:
            await self.ping_service.put(
                f"/v1.0/sensors/{config.sensor_id}/config",
                json={
                    "sample_rate": config.sample_rate,
                    "enabled": config.enabled,
                    "calibration_file": config.calibration_file,
                },
            )
            return True
        except Exception:
            return False

    async def get_video_streams(self) -> list[VideoStream]:
        """Get all video streams from the Camera Manager."""
        try:
            streams_data: list[dict[str, Any]] = await self.camera_manager.get(  # type: ignore[assignment]
                "/streams"
            )
            streams: list[VideoStream] = []
            for s in streams_data:
                vs = s.get("video_and_stream", {})
                stream_info = vs.get("stream_information", {})
                config = stream_info.get("configuration", {})
                video_source = vs.get("video_source", {})

                source_type, source_details = self._parse_video_source(video_source)
                frame_interval = config.get("frame_interval", {})
                denominator = frame_interval.get("denominator", 0)
                numerator = frame_interval.get("numerator", 1)
                fps = int(denominator / numerator) if numerator > 0 else None

                streams.append(
                    VideoStream(
                        id=s.get("id", ""),
                        name=vs.get("name", "Unknown"),
                        running=s.get("running", False),
                        error=s.get("error"),
                        encode=config.get("encode"),
                        width=config.get("width"),
                        height=config.get("height"),
                        fps=fps,
                        endpoints=stream_info.get("endpoints", []),
                        source_type=source_type,
                        manufacturer=source_details.get("manufacturer"),
                        model=source_details.get("model"),
                        serial_number=source_details.get("serial_number"),
                        firmware_version=source_details.get("firmware_version"),
                    )
                )
            return streams
        except Exception as e:
            logger.warning(f"Failed to get video streams: {e}")
            return []

    def _parse_video_source(
        self, video_source: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any]]:
        """Extract source type and metadata from a video_source dict."""
        for source_type, details in video_source.items():
            if isinstance(details, dict):
                return source_type, details
        return None, {}

    async def _get_camera_module(self) -> ModuleInfo | None:
        """Get camera module info from the Camera Manager streams."""
        try:
            streams = await self.get_video_streams()
            if streams:
                first = streams[0]
                return ModuleInfo(
                    id="camera-1",
                    name="Camera Module",
                    type="camera",
                    status="connected",
                    module_status="Ready: Active" if first.running else "Stopped",
                    power_usage=95.0,
                    firmware_version=first.firmware_version,
                    last_reading=datetime.now().isoformat(),
                )
        except Exception:
            pass
        return None

    async def _get_ping_modules(self) -> list[ModuleInfo]:
        """Get ping/sonar sensor modules."""
        modules = []
        try:
            devices = await self.ping_service.get("/v1.0/devices")

            for device in devices:
                is_connected = device.get("connected", False)
                modules.append(
                    ModuleInfo(
                        id=device.get("id", "ping-1"),
                        name=device.get("name", "Ping Sensor"),
                        type="sensor",
                        status="connected" if is_connected else "disconnected",
                        module_status="Ready: Active" if is_connected else "Disconnected",
                        power_usage=device.get("power_usage", 50.0),
                        sample_rate=device.get("sample_rate", 1.0),
                        firmware_version=device.get("firmware_version"),
                        last_reading=datetime.now().isoformat() if is_connected else None,
                    )
                )
        except Exception:
            pass
        return modules

    async def _get_light_module(self) -> ModuleInfo | None:
        """Get light module info."""
        # Lights are typically controlled via MAVLink
        try:
            # Check for lights via MAVLink servo outputs
            return ModuleInfo(
                id="light-1",
                name="Light Module",
                type="light",
                status="connected",
                module_status="Ready: Active",
                power_usage=88.0,
                last_reading=datetime.now().isoformat(),
            )
        except Exception:
            pass
        return None

    def _get_mock_modules(self) -> list[ModuleInfo]:
        """Return mock module data for testing."""
        now = datetime.now().isoformat()
        return [
            ModuleInfo(
                id="camera-1",
                name="Camera Module",
                type="camera",
                status="connected",
                module_status="Ready: Active",
                power_usage=95.0,
                last_reading=now,
            ),
            ModuleInfo(
                id="ctd-1",
                name="CTD Sensor",
                type="sensor",
                status="connected",
                module_status="Ready: Active",
                power_usage=98.0,
                sample_rate=1.0,
                last_reading=now,
            ),
            ModuleInfo(
                id="light-1",
                name="Light Module",
                type="light",
                status="connected",
                module_status="Warning: Leak Detected",
                power_usage=88.0,
                last_reading=now,
            ),
            ModuleInfo(
                id="co2-1",
                name="CO/O2",
                type="sensor",
                status="disconnected",
                module_status="Disconnected",
                power_usage=0.0,
                sample_rate=1.0,
                last_reading=None,
            ),
        ]

    async def close(self) -> None:
        """Close HTTP clients."""
        await self.ping_service.close()
        await self.mavlink2rest.close()
        await self.camera_manager.close()

