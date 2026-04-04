"""Dive control routes.

Exposes endpoints to start/stop dives by setting the DORIS_START
MAVLink parameter, and to query current dive status.

When starting a dive with a configuration name, the route loads
the configuration from storage and pushes DORIS_* params before
triggering.  A dive record (dive_NNNN.json) is persisted under
DATA_ROOT/dives/.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from robyn import Robyn

from ..services.camera import CameraService
from ..services.dive import DiveService
from ..services.storage import DATA_ROOT, StorageService

logger = logging.getLogger(__name__)

dive_service = DiveService()
storage_service = StorageService()
camera_service = CameraService()

DIVES_DIR = DATA_ROOT / "dives"


def _next_dive_filename() -> Path:
    """Return the next available dive_NNNN.json path."""
    DIVES_DIR.mkdir(parents=True, exist_ok=True)
    highest = 0
    pattern = re.compile(r"^dive_(\d{4})\.json$")
    for f in DIVES_DIR.iterdir():
        m = pattern.match(f.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return DIVES_DIR / f"dive_{highest + 1:04d}.json"


def _update_active_dive_record(new_status: str) -> None:
    """Find the most recent active dive record and update its status."""
    DIVES_DIR.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(r"^dive_(\d{4})\.json$")
    dive_files = []
    for f in DIVES_DIR.iterdir():
        m = pattern.match(f.name)
        if m:
            dive_files.append((int(m.group(1)), f))
    dive_files.sort(reverse=True)

    for _, dive_file in dive_files:
        try:
            record = json.loads(dive_file.read_text())
            if record.get("status") == "active":
                record["status"] = new_status
                record["ended_at"] = datetime.now(tz=timezone.utc).isoformat()
                dive_file.write_text(json.dumps(record, indent=2, default=str))
                logger.info(f"Dive record updated: {dive_file.name} -> {new_status}")
                return
        except Exception as e:
            logger.warning(f"Error reading {dive_file.name}: {e}")


def register_dive_routes(app: Robyn) -> None:
    @app.post("/api/v1/dive/start")
    async def start_dive(request):
        config = None
        try:
            body = json.loads(request.body) if request.body else {}
        except (json.JSONDecodeError, TypeError):
            body = {}

        config_name = body.get("configuration")
        if config_name:
            config = await storage_service.load_configuration(config_name)
            if config is None:
                return json.dumps({
                    "success": False,
                    "message": f"Configuration '{config_name}' not found",
                })
            logger.info(f"Starting dive with configuration: {config_name}")

        ok = await dive_service.start_dive(config=config)
        if not ok:
            return json.dumps({"success": False, "message": "Failed to set parameter"})

        dive_file = _next_dive_filename()
        dive_record = {
            "dive_name": body.get("dive_name", ""),
            "username": body.get("username", ""),
            "configuration": config_name or "",
            "estimated_depth": body.get("estimated_depth", ""),
            "release_weight_date": body.get("release_weight_date", ""),
            "release_weight_time": body.get("release_weight_time", ""),
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "status": "active",
        }
        if config:
            dive_record["configuration_snapshot"] = json.loads(
                config.model_dump_json()
            )

        try:
            dive_file.write_text(json.dumps(dive_record, indent=2, default=str))
            logger.info(f"Dive record saved: {dive_file.name}")
        except Exception as e:
            logger.warning(f"Failed to save dive record: {e}")

        msg = f"DORIS_START set to 1 (dive: {dive_file.name})"
        return json.dumps({"success": True, "message": msg, "dive_file": dive_file.name})

    @app.post("/api/v1/dive/stop")
    async def stop_dive():
        ok = await dive_service.stop_dive()

        # Stop video recording
        try:
            await camera_service.stop_recording()
            logger.info("Video recording stopped")
        except Exception as e:
            logger.warning(f"Failed to stop recording: {e}")

        # Update the most recent active dive record
        try:
            _update_active_dive_record("cancelled")
        except Exception as e:
            logger.warning(f"Failed to update dive record: {e}")

        return json.dumps({"success": ok, "message": "Dive cancelled" if ok else "Failed to set parameter"})

    @app.get("/api/v1/dive/status")
    async def dive_status():
        status = await dive_service.get_status()
        return json.dumps(status)
