"""Dive control routes.

Exposes endpoints to start/stop dives by setting the DORIS_START
MAVLink parameter, to query dive status, and to read persisted mission
state (mission_state.json under DATA_ROOT).

When starting a dive with a configuration name, the route loads
the configuration from storage and pushes DORIS_* params before
triggering. A dive record (dive_NNNN.json) is persisted under
DATA_ROOT/dives/.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from robyn import Response, Robyn

from ..services.camera import CameraService
from ..services.dive import DiveService
from ..services.storage import DATA_ROOT, StorageService

logger = logging.getLogger(__name__)

dive_service = DiveService()
storage_service = StorageService()
camera_service = CameraService()

DIVES_DIR = DATA_ROOT / "dives"
MISSION_STATE_PATH = DATA_ROOT / "mission_state.json"
PROFILE_SEQ_PATH = DATA_ROOT / "doris_profile_seq.txt"


def _allocate_profile_id() -> int:
    """Monotonic nonzero profile id for DORIS_PRF_ID (persisted across restarts)."""
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    n = 1
    if PROFILE_SEQ_PATH.exists():
        try:
            n = int(PROFILE_SEQ_PATH.read_text().strip()) + 1
        except ValueError:
            n = 1
    PROFILE_SEQ_PATH.write_text(str(n))
    return n


def _sync_mission_state_from_vehicle(status_dict: dict) -> None:
    """Advance mission_state.json when the vehicle leaves CONFIG or ends the dive."""
    if not MISSION_STATE_PATH.exists():
        return
    try:
        ms = json.loads(MISSION_STATE_PATH.read_text())
    except Exception:
        return
    st = ms.get("status")
    if st in ("completed", "cancelled"):
        return
    active = bool(status_dict.get("active", False))
    ds = status_dict.get("doris_script_state")
    changed = False
    if st == "pending" and active and ds is not None and int(ds) >= 0:
        ms["status"] = "active"
        ms["activated_at"] = datetime.now(tz=timezone.utc).isoformat()
        changed = True
    elif st == "active" and not active:
        ms["status"] = "completed"
        ms["completed_at"] = datetime.now(tz=timezone.utc).isoformat()
        changed = True
    if changed:
        try:
            MISSION_STATE_PATH.write_text(json.dumps(ms, indent=2, default=str))
        except Exception as e:
            logger.warning("Failed to update mission_state.json: %s", e)


def _write_mission_state(payload: dict) -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        MISSION_STATE_PATH.write_text(json.dumps(payload, indent=2, default=str))
    except Exception as e:
        logger.warning("Failed to write mission_state.json: %s", e)


def _set_mission_terminal_status(new_status: str) -> None:
    if new_status not in ("cancelled", "completed"):
        return
    if not MISSION_STATE_PATH.exists():
        return
    try:
        ms = json.loads(MISSION_STATE_PATH.read_text())
        ms["status"] = new_status
        key = "cancelled_at" if new_status == "cancelled" else "completed_at"
        ms[key] = datetime.now(tz=timezone.utc).isoformat()
        MISSION_STATE_PATH.write_text(json.dumps(ms, indent=2, default=str))
    except Exception as e:
        logger.warning("Failed to set mission status %s: %s", new_status, e)


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

        loaded_at = datetime.now(tz=timezone.utc)
        upload_date = float(loaded_at.year * 10_000 + loaded_at.month * 100 + loaded_at.day)
        upload_time = float(loaded_at.hour * 100 + loaded_at.minute)
        profile_id = _allocate_profile_id() if config is not None else 0

        ok = await dive_service.start_dive(
            config=config,
            profile_id=profile_id if config is not None else None,
            upload_date=upload_date if config is not None else None,
            upload_time=upload_time if config is not None else None,
        )
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
            "started_at": loaded_at.isoformat(),
            "status": "active",
            "profile_id": profile_id,
        }
        lat, lon = body.get("latitude"), body.get("longitude")
        if lat is not None and lon is not None:
            try:
                dive_record["latitude"] = float(lat)
                dive_record["longitude"] = float(lon)
            except (TypeError, ValueError):
                pass
        loc_str = body.get("location")
        if isinstance(loc_str, str) and loc_str.strip():
            dive_record["location"] = loc_str.strip()
        if config:
            dive_record["configuration_snapshot"] = json.loads(
                config.model_dump_json()
            )

        try:
            dive_file.write_text(json.dumps(dive_record, indent=2, default=str))
            logger.info(f"Dive record saved: {dive_file.name}")
        except Exception as e:
            logger.warning(f"Failed to save dive record: {e}")

        _write_mission_state({
            "status": "pending",
            "configuration_name": config_name or "",
            "loaded_at": loaded_at.isoformat(),
            "profile_id": profile_id,
            "dive_file": dive_file.name,
        })

        msg = f"DORIS_START set to 1 (dive: {dive_file.name})"
        return json.dumps({
            "success": True,
            "message": msg,
            "dive_file": dive_file.name,
            "profile_id": profile_id,
        })

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

        try:
            _set_mission_terminal_status("cancelled")
        except Exception as e:
            logger.warning(f"Failed to update mission state: {e}")

        return json.dumps({"success": ok, "message": "Dive cancelled" if ok else "Failed to set parameter"})

    @app.post("/api/v1/dive/sitl/simulate_drop")
    async def sitl_simulate_drop(request):
        """ArduSub SITL only: apply negative SIM_BUOYANCY so the vehicle sinks past DORIS_DPT_GAT."""
        try:
            body = json.loads(request.body) if request.body else {}
        except (json.JSONDecodeError, TypeError):
            body = {}
        try:
            buoyancy = float(body.get("buoyancy_newtons", -19.6))
        except (TypeError, ValueError):
            buoyancy = -19.6
        ok = await dive_service.set_sim_buoyancy(buoyancy)
        return json.dumps({
            "success": ok,
            "message": "SIM_BUOYANCY set (SITL sink)" if ok else "Failed to set SIM_BUOYANCY",
            "buoyancy_newtons": buoyancy,
        })

    @app.get("/api/v1/dive/mission")
    async def dive_mission():
        status = await dive_service.get_status()
        _sync_mission_state_from_vehicle(status)
        if not MISSION_STATE_PATH.exists():
            return json.dumps({"mission": None})
        try:
            mission = json.loads(MISSION_STATE_PATH.read_text())
            return json.dumps({"mission": mission})
        except Exception:
            return json.dumps({"mission": None})

    @app.get("/api/v1/dive/status")
    async def dive_status():
        status = await dive_service.get_status()
        _sync_mission_state_from_vehicle(status)
        return json.dumps(status)

    @app.get("/api/v1/dive/history")
    async def dive_history_list():
        """List persisted dives (dives/dive_*.json) for the Previous Dives page."""
        try:
            entries = await storage_service.list_dive_history()
            return json.dumps([e.model_dump(mode="json") for e in entries])
        except Exception as e:
            logger.exception("Failed to list dive history")
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.delete("/api/v1/dive/history/:dive_id")
    async def dive_history_delete(request):
        """Remove a dive record JSON (does not delete recorder media files)."""
        dive_id = request.path_params.get("dive_id", "").strip()
        try:
            ok = await storage_service.delete_dive_record(dive_id)
        except Exception as e:
            logger.exception("Failed to delete dive record")
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )
        if not ok:
            return Response(
                status_code=404,
                description=json.dumps({"error": "Dive record not found"}),
                headers={"Content-Type": "application/json"},
            )
        return json.dumps({"success": True})
