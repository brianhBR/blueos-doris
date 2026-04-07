"""System-related models."""

from pydantic import BaseModel


class StorageInfo(BaseModel):
    """Storage information."""

    total_gb: float
    used_gb: float
    available_gb: float
    used_percent: float  # Frontend expects used_percent
    storage_type: str = "SD Card"


class BatteryInfo(BaseModel):
    """Battery information."""

    level: float  # Frontend expects 'level' not 'percent'
    voltage: float | None = None
    current: float | None = None
    temperature: float | None = None
    time_remaining: str = "Unknown"  # Frontend expects formatted string
    charging: bool = False


class LocationInfo(BaseModel):
    """GPS location information."""

    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    depth: float = 0.0
    heading: float = 0.0
    speed: float = 0.0
    fix_type: str = "none"
    satellites: int = 0
    last_update: str = "Never"  # Frontend expects formatted string


class SystemStatus(BaseModel):
    """Overall system status."""

    connected: bool = False
    battery_level: float = 0.0
    battery_voltage: float = 0.0
    battery_time_remaining: str = "Unknown"
    storage_used_percent: float = 0.0
    storage_used_gb: float = 0.0
    storage_total_gb: float = 0.0
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    temperature: float | None = None
    uptime: str = "0:00:00"

