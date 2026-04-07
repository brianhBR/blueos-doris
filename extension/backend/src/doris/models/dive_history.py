"""Persisted dive records (dives/dive_*.json) exposed for the Previous Dives UI."""

from datetime import datetime

from pydantic import BaseModel


class DiveHistoryEntry(BaseModel):
    """Summary of one saved dive record for listing."""

    id: str
    name: str
    status: str
    date: datetime
    duration: str
    location: str | None = None
    max_depth: float | None = None
    image_count: int = 0
    video_count: int = 0
    configuration: str = ""
