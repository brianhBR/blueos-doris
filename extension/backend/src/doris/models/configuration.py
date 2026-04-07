"""Deployment configuration models.

Mirrors the settings from the frontend Configuration (MissionProgramming) tab.
Each configuration is persisted as a JSON file in DATA_ROOT/configurations/.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CameraType(StrEnum):
    CONTINUOUS_VIDEO = "continuous-video"
    TIMELAPSE = "timelapse"
    VIDEO_INTERVAL = "video-interval"


class LightMode(StrEnum):
    CONTINUOUS = "continuous"
    INTERVAL = "interval"


class TimeValue(BaseModel):
    """A duration expressed as a number + unit pair."""

    number: str = "0"
    unit: Literal["seconds", "minutes", "hours"] = "seconds"


class CameraSettings(BaseModel):
    enabled: bool = False
    camera_type: CameraType = CameraType.CONTINUOUS_VIDEO

    capture_frequency: int = 10
    capture_frequency_unit: Literal["seconds", "minutes", "hours"] = "seconds"

    video_record: TimeValue = Field(default_factory=lambda: TimeValue(number="10", unit="seconds"))
    video_pause: TimeValue = Field(default_factory=lambda: TimeValue(number="5", unit="seconds"))

    resolution: str = "4K"
    image_type: str = "High-Rez JPG"
    file_format: str = "JPEG"
    video_file_format: str = ".MP4"
    frame_rate: int = 30
    focus: str = "auto"
    iso: str = "auto"
    white_balance: str = "auto"
    exposure: str = "0"
    sharpness: str = "medium"

    sleep_timer_enabled: bool = False
    sleep_timer: TimeValue = Field(default_factory=lambda: TimeValue(number="", unit="hours"))


class LightSettings(BaseModel):
    enabled: bool = False
    mode: LightMode = LightMode.CONTINUOUS
    brightness: int = 75
    match_camera_interval: bool = False

    on_time: TimeValue = Field(default_factory=lambda: TimeValue(number="10", unit="seconds"))
    off_time: TimeValue = Field(default_factory=lambda: TimeValue(number="5", unit="seconds"))


class DescentPhase(BaseModel):
    camera: CameraSettings = Field(default_factory=CameraSettings)
    light: LightSettings = Field(default_factory=LightSettings)


class BottomPhase(BaseModel):
    camera: CameraSettings = Field(default_factory=lambda: CameraSettings(enabled=True))
    camera_delay: TimeValue = Field(default_factory=lambda: TimeValue(number="30", unit="seconds"))
    light: LightSettings = Field(default_factory=lambda: LightSettings(enabled=True))
    light_delay: TimeValue = Field(default_factory=lambda: TimeValue(number="30", unit="seconds"))


class ReleaseWeight(BaseModel):
    method: Literal["elapsed", "datetime"] = "elapsed"
    elapsed: TimeValue = Field(default_factory=lambda: TimeValue(number="6", unit="hours"))
    release_date: str = "2026-02-02"
    release_time: str = "12:00"


class AscentPhase(BaseModel):
    same_as_descent: bool = False
    camera: CameraSettings = Field(default_factory=CameraSettings)
    light: LightSettings = Field(default_factory=LightSettings)
    release_weight: ReleaseWeight = Field(default_factory=ReleaseWeight)


class RecoverySettings(BaseModel):
    activate_mast_light: bool = False
    update_frequency: str = "5min"
    use_iridium: bool = False
    use_lora: bool = False


class DeploymentConfiguration(BaseModel):
    """Full deployment configuration saved to disk."""

    name: str
    dive_name: str = "Dive II"
    estimated_depth: str = ""
    descent: DescentPhase = Field(default_factory=DescentPhase)
    bottom: BottomPhase = Field(default_factory=BottomPhase)
    ascent: AscentPhase = Field(default_factory=AscentPhase)
    recovery: RecoverySettings = Field(default_factory=RecoverySettings)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class ConfigurationSummary(BaseModel):
    """Lightweight listing entry returned by the list endpoint."""

    name: str
    created_at: datetime
    updated_at: datetime
