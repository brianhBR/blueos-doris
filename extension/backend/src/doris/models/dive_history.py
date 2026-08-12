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
    # location: start fix (kept for back-compat); start/end split out below.
    location: str | None = None
    start_location: str | None = None
    end_location: str | None = None
    # max_depth: recorder log when available, else user estimate (see storage layer).
    max_depth: float | None = None
    estimated_depth_m: float | None = None
    log_max_depth_m: float | None = None
    mcap_relative_path: str | None = None
    image_count: int = 0
    video_count: int = 0
    configuration: str = ""
    # ArduPilot BIN log archive results (populated by services.binlog).
    # ``bin_log_files`` is a list of media-download ids (relative to USB or
    # DATA_ROOT) that the frontend can pass to ``/api/v1/media/download``.
    bin_log_files: list[str] = []
    bin_log_status: str | None = None
    # Deferred post-dive processing (services.dive_processing).  ``pending``
    # means the dive is quiesced and waiting for the operator to press
    # "Process Dive"; older records predate the field and report None.
    processing_state: str | None = None
    processing_finished_at: datetime | None = None
    processing_error: str | None = None
