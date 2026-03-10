"""Media-related models."""

from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel


class MediaType(StrEnum):
    """Type of media file."""

    IMAGE = "image"
    VIDEO = "video"
    DATA = "data"  # sensor data files


class MediaFile(BaseModel):
    """A media file record."""

    id: str
    filename: str
    media_type: MediaType
    size_bytes: int
    duration_seconds: float | None = None  # for videos
    resolution: str | None = None
    created_at: datetime
    mission_id: str | None = None
    thumbnail_url: str | None = None
    download_url: str
    is_synced: bool = False


class MediaMission(BaseModel):
    """Media grouped by mission."""

    mission_id: str
    mission_name: str
    date: datetime
    image_count: int
    video_count: int
    data_file_count: int
    total_size_bytes: int
    thumbnail_url: str | None = None


class MediaFilter(BaseModel):
    """Filter options for media queries."""

    mission_id: str | None = None
    media_type: MediaType | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    search: str | None = None


class SyncStatus(BaseModel):
    """Cloud sync status."""

    is_syncing: bool = False
    pending_files: int = 0
    synced_files: int = 0
    total_files: int = 0
    last_sync: datetime | None = None
    error: str | None = None

