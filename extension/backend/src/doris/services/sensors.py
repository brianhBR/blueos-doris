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

        # Get camera modules (one per video stream)
        camera_modules = await self._get_camera_modules()
        modules.extend(camera_modules)

        # Get ping/sonar sensors
        ping_modules = await self._get_ping_modules()
        modules.extend(ping_modules)

        return modules

    async def get_sensor_readings(self, sensor_id: str) -> list[SensorReading]:
        """Get recent readings from a sensor.

        The Ping Service API does not expose per-sensor reading history,
        so this returns an empty list.
        """
        return []

    async def configure_sensor(self, config: SensorConfig) -> bool:
        """Update sensor configuration via POST /v1.0/sensors."""
        try:
            await self.ping_service.post(
                "/v1.0/sensors",
                json={
                    "sensor_id": config.sensor_id,
                    "sample_rate": config.sample_rate,
                    "enabled": config.enabled,
                    "calibration_file": config.calibration_file,
                },
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to configure sensor {config.sensor_id}: {e}")
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

    async def _get_camera_modules(self) -> list[ModuleInfo]:
        """Get a ModuleInfo for each video stream from the Camera Manager."""
        modules: list[ModuleInfo] = []
        try:
            streams = await self.get_video_streams()
            for stream in streams:
                modules.append(
                    ModuleInfo(
                        id=f"camera-{stream.id}",
                        name=f"Camera ({stream.name})",
                        type="camera",
                        status="connected" if stream.running else "disconnected",
                        module_status="Ready: Active" if stream.running else "Stopped",
                        firmware_version=stream.firmware_version,
                        last_reading=datetime.now().isoformat() if stream.running else None,
                    )
                )
        except Exception as e:
            logger.warning(f"Failed to get camera modules: {e}")
        return modules

    async def _get_ping_modules(self) -> list[ModuleInfo]:
        """Get ping/sonar sensor modules from GET /v1.0/sensors."""
        modules: list[ModuleInfo] = []
        try:
            devices: list[dict[str, Any]] = await self.ping_service.get(  # type: ignore[assignment]
                "/v1.0/sensors"
            )

            for device in devices:
                device_id = device.get("device_id", 0)
                ping_type = device.get("ping_type", "Ping")
                fw_major = device.get("firmware_version_major", 0)
                fw_minor = device.get("firmware_version_minor", 0)
                fw_patch = device.get("firmware_version_patch", 0)
                firmware = f"{fw_major}.{fw_minor}.{fw_patch}"
                port = device.get("port", "")

                modules.append(
                    ModuleInfo(
                        id=f"ping-{device_id}",
                        name=f"{ping_type} ({port})" if port else ping_type,
                        type="sensor",
                        status="connected",
                        module_status="Ready: Active",
                        firmware_version=firmware,
                        last_reading=datetime.now().isoformat(),
                    )
                )
        except Exception as e:
            logger.warning(f"Failed to get ping sensors: {e}")
        return modules

    async def close(self) -> None:
        """Close HTTP clients."""
        await self.ping_service.close()
        await self.mavlink2rest.close()
        await self.camera_manager.close()

