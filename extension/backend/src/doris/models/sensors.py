"""Sensor and module models."""

from datetime import datetime

from pydantic import BaseModel


class ModuleInfo(BaseModel):
    """Information about a connected module."""

    id: str
    name: str
    type: str  # Module type (camera, sensor, light)
    status: str  # Connection status for frontend (connected/disconnected/error)
    module_status: str  # Human-readable status message
    last_reading: str | None = None  # Last reading timestamp
    power_usage: float = 0.0  # percentage
    sample_rate: float | None = None  # Hz, for sensors
    firmware_version: str | None = None


class VideoStream(BaseModel):
    """A video stream from the Camera Manager."""

    id: str
    name: str
    running: bool
    error: str | None = None
    encode: str | None = None
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    endpoints: list[str] = []
    source_type: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    firmware_version: str | None = None


class SensorReading(BaseModel):
    """A sensor data reading."""

    sensor_id: str
    sensor_name: str
    value: float
    unit: str
    timestamp: datetime
    quality: float = 1.0  # 0-1, data quality indicator


class SensorConfig(BaseModel):
    """Configuration for a sensor."""

    sensor_id: str
    sample_rate: float  # Hz
    enabled: bool = True
    calibration_file: str | None = None

