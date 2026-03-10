"""Mission-related models."""

from datetime import datetime as dt
from enum import StrEnum
from pydantic import BaseModel


class TriggerType(StrEnum):
    """Type of mission trigger."""

    MANUAL = "manual"
    TIME_DATE = "time_date"
    DEPTH = "depth"
    DURATION = "duration"


class MissionStatus(StrEnum):
    """Status of a mission."""

    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TriggerConfig(BaseModel):
    """Configuration for a mission trigger."""

    trigger_type: TriggerType
    value: float | None = None  # depth in meters, duration in seconds
    scheduled_time: dt | None = None  # for time/date trigger
    unit: str | None = None  # "seconds", "minutes", "hours", "meters"


class CameraSettings(BaseModel):
    """Camera configuration for a mission."""

    resolution: str = "4K"  # 1080p, 2.7K, 4K
    frame_rate: int = 30  # 24, 30, 60 fps
    focus: str = "auto"  # auto, fixed


class MissionConfig(BaseModel):
    """Configuration for a new mission."""

    name: str
    start_trigger: TriggerConfig
    end_trigger: TriggerConfig
    timelapse_enabled: bool = False
    timelapse_interval: int = 60  # seconds
    camera_settings: CameraSettings = CameraSettings()
    lighting_brightness: int = 75  # percentage
    sensors_enabled: list[str] = []


class Mission(BaseModel):
    """A mission record."""

    id: str
    name: str
    status: MissionStatus
    config: MissionConfig
    created_at: dt
    started_at: dt | None = None
    completed_at: dt | None = None
    duration_seconds: int | None = None
    location: str | None = None  # GPS coordinates as string
    max_depth: float | None = None
    image_count: int = 0
    video_count: int = 0


class MissionSummary(BaseModel):
    """Summary of a mission for listing."""

    id: str
    name: str
    status: MissionStatus
    date: dt
    duration: str  # formatted duration
    location: str | None = None
    max_depth: float | None = None
    image_count: int = 0
    video_count: int = 0

