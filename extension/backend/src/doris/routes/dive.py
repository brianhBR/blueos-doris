"""Dive control routes.

Exposes endpoints to start/stop dives by setting the DORIS_START
MAVLink parameter, to query dive status, and to read persisted mission
state (mission_state.json under DATA_ROOT).

When starting a dive with a configuration name, the route loads
the configuration from storage and pushes DORIS_* params before
triggering. A dive record (dive_NNNN.json) is persisted under
DATA_ROOT/dives/.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from robyn import Response, Robyn

from ..services import binlog, dive_csv_export
from ..services import ip_camera_recorder as iprec
from ..services.camera import CameraService
from ..services.dive import DiveService
from ..services.dive_processing import dive_processing_service, quiesce_dive
from ..services.dive_records import (
    find_latest_active_dive_record,
    set_mission_terminal_status,
    update_active_dive_record,
    write_json_atomic,
)
from ..services.frame import FrameService
from ..services.safe_surface import safe_surface_service
from ..services.storage import DATA_ROOT, StorageService, media_download_id_from_abs_path
from ..services.tracker import tracker_service

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
    elif st in ("pending", "active") and not active:
        # This runs off UI polling, so it can arrive long after the dive ended
        # -- including after the AGT cut power and the operator power cycled on
        # deck, by which point Lua has restarted in CONFIG and reports neither
        # active nor completed.  Treating that as a cancellation would mislabel
        # every successful dive, so an absent recovery signal only downgrades
        # the mission when nothing else witnessed the end of the dive.
        completed = bool(status_dict.get("completed", False)) or _recovery_was_seen()
        new_status = "completed" if completed else "cancelled"
        ms["status"] = new_status
        ms[f"{new_status}_at"] = datetime.now(tz=timezone.utc).isoformat()
        changed = True
        try:
            _update_active_dive_record(new_status)
        except Exception as e:
            logger.warning("Failed to mark dive record %s: %s", new_status, e)
    if changed:
        _write_mission_state({k: v for k, v in ms.items() if not k.startswith("_")})


def _recovery_was_seen() -> bool:
    """True if anything already observed this dive reach RECOVERY.

    Quiesce stamps the dive record when Lua enters RECOVERY, and the
    safe-surface service latches the same observation from the MAVLink stream.
    Either is proof the dive finished rather than being abandoned.
    """
    if safe_surface_service.recovery_seen():
        return True
    record = _find_latest_active_dive_record()
    if record is None:
        return False
    return bool(record.get("dive_stamp")) and record.get("processing_state") is not None


def _write_dive_record(dive_file: Path, record: dict) -> None:
    write_json_atomic(dive_file, record)


def _write_mission_state(payload: dict) -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        write_json_atomic(MISSION_STATE_PATH, payload)
    except Exception as e:
        logger.warning("Failed to write mission_state.json: %s", e)


def _set_mission_terminal_status(new_status: str) -> None:
    set_mission_terminal_status(MISSION_STATE_PATH, new_status)


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


def _update_active_dive_record(new_status: str) -> Path | None:
    """Close the most recent active dive record; returns its path or None."""
    return update_active_dive_record(DIVES_DIR, new_status)


def _find_latest_active_dive_record() -> dict | None:
    """Most recent 'active' dive record, used to reconstruct the banner
    configuration name after a restart/reconnect (issue #38)."""
    return find_latest_active_dive_record(DIVES_DIR)


def _close_all_active_dive_records(new_status: str = "completed") -> int:
    """Close every dive record still marked 'active'. Returns count closed."""
    DIVES_DIR.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(r"^dive_(\d{4})\.json$")
    closed = 0
    for f in DIVES_DIR.iterdir():
        if not pattern.match(f.name):
            continue
        try:
            record = json.loads(f.read_text())
            if record.get("status") == "active":
                record["status"] = new_status
                try:
                    ended = datetime.fromtimestamp(
                        f.stat().st_mtime, tz=timezone.utc
                    ).isoformat()
                except OSError:
                    ended = datetime.now(tz=timezone.utc).isoformat()
                record["ended_at"] = ended
                record.setdefault("processing_state", "pending")
                _write_dive_record(f, record)
                logger.info(f"Stale dive closed: {f.name} -> {new_status}")
                closed += 1
        except Exception as e:
            logger.warning(f"Error closing {f.name}: {e}")
    return closed


def register_dive_routes(app: Robyn) -> None:
    @app.post("/api/v1/dive/start")
    async def start_dive(request):
        firmware, frame = await asyncio.gather(
            tracker_service.get_version(request=True),
            FrameService().get_frame_status(),
        )
        readiness = safe_surface_service.evaluate_release_readiness(firmware, frame)
        if not readiness["ready"]:
            return Response(
                status_code=409,
                description=json.dumps({
                    "success": False,
                    "error": "No usable weight release path",
                    "blockers": readiness["blockers"],
                }),
                headers={"Content-Type": "application/json"},
            )
        for warning in readiness["warnings"]:
            logger.warning("Release path degraded at mission start: %s", warning)

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
        else:
            # No configuration provided: the dive proceeds with whatever
            # DORIS_* params are currently on the autopilot, which may be
            # leftovers from a prior dive setup.  Most importantly,
            # DORIS_BTM_TIM (the bottom-time release timer) keeps its
            # previous value, which can produce surprisingly long dives
            # when the operator expected the new profile's value to
            # apply.  Real dive starts from the UI always send
            # `configuration:`; this branch typically indicates a test
            # script that misnamed the field (e.g. `name:`) -- be loud
            # so the misuse is visible in logs.
            other_keys = sorted(k for k in body.keys() if k != "configuration")
            logger.warning(
                "DIVE START with no configuration -- keeping current "
                "DORIS_* params (incl. DORIS_BTM_TIM bottom timer); "
                "body keys: %s.  Use 'configuration': '<name>' to push "
                "fresh params.",
                other_keys or "(empty body)",
            )

        loaded_at = datetime.now(tz=timezone.utc)
        clock_sane = 2024 <= loaded_at.year <= 2030

        if not clock_sane:
            rw_date = body.get("release_weight_date", "")
            rw_time = body.get("release_weight_time", "00:00")
            if rw_date:
                try:
                    loaded_at = datetime.fromisoformat(
                        f"{rw_date}T{rw_time}:00+00:00"
                    )
                    logger.warning(
                        "System clock is wrong (year %d); using user-entered "
                        "release_weight_date %s as started_at",
                        datetime.now(tz=timezone.utc).year,
                        rw_date,
                    )
                except ValueError:
                    logger.warning(
                        "System clock is wrong and release_weight_date is "
                        "unparseable (%s); timestamps will be inaccurate",
                        rw_date,
                    )

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

        # Clear any stale dive-scoped recorder state (base_stamp,
        # snapshot sequence counters) left over from a prior UI session
        # or an aborted dive.  Ensures this new dive's recordings get a
        # fresh ``radcam_<stamp>_...`` prefix instead of sharing one with
        # a previous session that never ran finalize.
        try:
            iprec.clear_snapshot_state()
        except Exception as e:
            logger.warning("clear_snapshot_state on dive start failed: %s", e)

        stale = _close_all_active_dive_records("completed")
        if stale:
            logger.info(f"Closed {stale} stale active dive record(s) before new dive")

        dive_file = _next_dive_filename()
        # Snapshot the most recent ArduPilot BIN log number so that on
        # dive end we can copy *only* the BINs created during this dive.
        try:
            bin_log_start_num = binlog.read_lastlog_num()
        except Exception as e:
            logger.warning("Failed to read ArduPilot LASTLOG.TXT at dive start: %s", e)
            bin_log_start_num = None

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
            "bin_log_start_num": bin_log_start_num,
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

        # Stop video recording (MCM) and IP camera extension recorder
        try:
            await camera_service.stop_recording()
            logger.info("Video recording stopped")
        except Exception as e:
            logger.warning(f"Failed to stop recording: {e}")
        try:
            await iprec.stop_recording()
            logger.info("IP camera extension recording stopped")
        except Exception as e:
            logger.warning(f"Failed to stop IP camera recorder: {e}")

        # Update the most recent active dive record.  A cancelled dive still
        # has recoverable data, so it is left marked for processing rather than
        # exported here: the operator runs Process Dive when they are ready.
        try:
            _update_active_dive_record("cancelled")
        except Exception as e:
            logger.warning(f"Failed to update dive record: {e}")

        try:
            _set_mission_terminal_status("cancelled")
        except Exception as e:
            logger.warning(f"Failed to update mission state: {e}")

        return json.dumps({"success": ok, "message": "Dive cancelled" if ok else "Failed to set parameter"})

    @app.post("/api/v1/dive/finalize")
    async def dive_finalize(request):
        """Quiesce the dive so payload power can be cut safely.

        Called by the Lua dive script on RECOVERY entry (fire-and-forget).
        This is deliberately cheap: it stops the recorder so the last video
        segment closes cleanly, records how the dive should later be
        processed, and closes the dive record while we still have proof the
        dive reached RECOVERY.  It must finish in seconds, because the AGT is
        waiting on it before cutting power to the Pi, camera, and lights.

        The expensive work -- ffmpeg, USB copies, telemetry parsing -- is
        deferred to ``POST /dive/history/:dive_id/process``, which the
        operator triggers from the Previous Dives page on deck.

        Optional inputs (query string OR JSON body):

        * ``stamp``: dive stamp; defaults to the recorder's current
          ``base_stamp``.
        * ``bottom_mode``: ``DORIS_BTM_CMOD`` value (1=continuous,
          2=interval, 3=timelapse).  Stored on the dive record so the
          deferred processing job can pick the right bottom-phase strategy
          long after the autopilot parameter has changed.
        """
        try:
            body = json.loads(request.body) if request.body else {}
        except (json.JSONDecodeError, TypeError):
            body = {}
        stamp = request.query_params.get("stamp", "")
        if stamp in (None, ""):
            stamp = body.get("stamp") or None
        # Accept ``bottom_mode`` from the query string OR the JSON body
        # so the Lua HTTP emitter (which doesn't construct a body) can
        # pass it as a query param without a Content-Length header.
        bm_raw = request.query_params.get("bottom_mode", "")
        if bm_raw in (None, ""):
            bm_raw = body.get("bottom_mode")
        bottom_mode: int | None = None
        if bm_raw not in (None, ""):
            try:
                bottom_mode = int(bm_raw)
            except (TypeError, ValueError):
                bottom_mode = None

        result = await quiesce_dive(stamp=stamp, bottom_mode=bottom_mode)
        return Response(
            status_code=200,
            description=json.dumps(result, default=str),
            headers={"Content-Type": "application/json"},
        )

    @app.post("/api/v1/dive/history/:dive_id/process")
    async def dive_history_process(request):
        """Start deferred post-dive processing for one dive."""
        dive_id = request.path_params.get("dive_id", "").strip()
        if not re.fullmatch(r"dive_\d{4}", dive_id):
            return Response(
                status_code=400,
                description=json.dumps({"error": "Invalid dive id"}),
                headers={"Content-Type": "application/json"},
            )
        dive_file = DIVES_DIR / f"{dive_id}.json"
        if not dive_file.exists():
            return Response(
                status_code=404,
                description=json.dumps({"error": f"{dive_id} not found"}),
                headers={"Content-Type": "application/json"},
            )
        try:
            session_id = dive_processing_service.start(dive_file)
        except RuntimeError as e:
            return Response(
                status_code=409,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )
        return json.dumps({"session_id": session_id, "dive_id": dive_id})

    @app.get("/api/v1/dive/process/status")
    async def dive_process_status(request):
        """Poll a processing run. Query params: session_id, from_line.

        Omitting ``session_id`` returns the most recent run, so the UI can
        recover its progress view after a reload.
        """
        session_id = request.query_params.get("session_id", "")
        if session_id:
            session = dive_processing_service.get_session(session_id)
        else:
            session = dive_processing_service.active_session()
        if session is None:
            return json.dumps({"session": None})

        from_line = 0
        try:
            from_line = int(request.query_params.get("from_line", "0") or "0")
        except (TypeError, ValueError):
            from_line = 0

        payload = session.to_dict()
        payload["lines"] = session.lines[from_line:]
        payload["total_lines"] = len(session.lines)
        return json.dumps({"session": payload}, default=str)

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
        mission: dict | None = None
        if MISSION_STATE_PATH.exists():
            try:
                mission = json.loads(MISSION_STATE_PATH.read_text())
            except Exception:
                mission = None

        # Issue #38: after a vehicle restart or device reconnect the
        # mission state can be missing or lose its configuration name while
        # the dive is still active, so the banner falls back to a bare
        # "Active Dive".  Reconstruct the configuration name from the
        # persisted active dive record (which survives restarts) and heal
        # mission_state.json so subsequent reads are consistent.
        needs_config = mission is None or not str(
            mission.get("configuration_name") or ""
        ).strip()
        if needs_config:
            record = _find_latest_active_dive_record()
            if record is not None:
                config_name = str(record.get("configuration") or "").strip()
                if config_name:
                    if mission is None:
                        mission = {
                            "status": "active",
                            "configuration_name": config_name,
                            "loaded_at": record.get("started_at", ""),
                            "profile_id": record.get("profile_id", 0),
                            "dive_file": record.get("_dive_file", ""),
                        }
                    else:
                        mission["configuration_name"] = config_name
                    _write_mission_state(
                        {k: v for k, v in mission.items() if not k.startswith("_")}
                    )

        if mission is None:
            return json.dumps({"mission": None})
        return json.dumps({"mission": mission})

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

    @app.get("/api/v1/dive/history/:dive_id/export/scientific.csv")
    async def dive_history_scientific_csv(request):
        """CSV with a dive-data header plus per-cycle system + science telemetry
        reassembled from the dive's primary .mcap."""
        dive_id = request.path_params.get("dive_id", "").strip()
        if not re.fullmatch(r"dive_\d{4}", dive_id):
            return Response(
                status_code=400,
                description=json.dumps({"error": "Invalid dive id"}),
                headers={"Content-Type": "application/json"},
            )
        path = DIVES_DIR / f"{dive_id}.json"
        if not path.is_file():
            return Response(
                status_code=404,
                description=json.dumps({"error": "Dive record not found"}),
                headers={"Content-Type": "application/json"},
            )
        try:
            dive_data = json.loads(path.read_text())
        except Exception as e:
            logger.exception("Failed to read dive record")
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

        from ..services.mcap_telemetry import dive_csv_filename

        download_name = dive_csv_filename(dive_data, dive_id)

        # Prefer a CSV already generated to USB at dive end -- serving it
        # is instant, whereas a fresh parse of a large .mcap can take tens
        # of seconds on the vehicle's Raspberry Pi.
        cached = dive_csv_export.find_cached_csv(dive_data)
        if cached is not None:
            try:
                raw = cached.read_bytes()
            except OSError as e:
                logger.warning("Cached CSV unreadable (%s); regenerating: %s", cached, e)
            else:
                return Response(
                    status_code=200,
                    description=raw,
                    headers={
                        "Content-Type": "text/csv; charset=utf-8",
                        "Content-Disposition": f'attachment; filename="{download_name}"',
                        "Content-Length": str(len(raw)),
                    },
                )

        from ..services.mcap_telemetry import (
            McapSummary,
            build_dive_csv,
            map_dive_stem_to_largest_mcap,
            summarize_mcap,
        )
        from ..services.storage import _load_dive_windows

        windows = _load_dive_windows(DATA_ROOT)
        mcap_map = map_dive_stem_to_largest_mcap(DATA_ROOT, windows)
        mcap_path = mcap_map.get(dive_id)
        rel: str | None = None
        summary = McapSummary()
        if mcap_path is not None:
            rel = media_download_id_from_abs_path(mcap_path, DATA_ROOT)
            try:
                summary = summarize_mcap(mcap_path)
            except Exception as e:
                logger.warning("MCAP summarize failed for %s: %s", mcap_path, e)

        csv_text = build_dive_csv(dive_data, summary, rel)
        raw = csv_text.encode("utf-8")
        return Response(
            status_code=200,
            description=raw,
            headers={
                "Content-Type": "text/csv; charset=utf-8",
                "Content-Disposition": f'attachment; filename="{download_name}"',
                "Content-Length": str(len(raw)),
            },
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

    @app.post("/api/v1/dive/history/:dive_id/archive_binlogs")
    async def dive_history_archive_binlogs(request):
        """Re-run the BIN log archive for a past dive.

        Useful when the USB drive was missing at dive end, or when the
        operator wants a fresh copy.  Returns the same status dict the
        background archiver writes into the dive record.
        """
        dive_id = request.path_params.get("dive_id", "").strip()
        if not re.fullmatch(r"dive_\d{4}", dive_id):
            return Response(
                status_code=400,
                description=json.dumps({"error": "Invalid dive id"}),
                headers={"Content-Type": "application/json"},
            )
        path = DIVES_DIR / f"{dive_id}.json"
        if not path.is_file():
            return Response(
                status_code=404,
                description=json.dumps({"error": "Dive record not found"}),
                headers={"Content-Type": "application/json"},
            )
        try:
            result = await binlog.archive_dive_bin_logs(path)
        except Exception as e:
            logger.exception("Manual BIN archive failed for %s", dive_id)
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )
        return Response(
            status_code=200,
            description=json.dumps(result, default=str),
            headers={"Content-Type": "application/json"},
        )

    @app.post("/api/v1/dive/history/:dive_id/export_csv_to_usb")
    async def dive_history_export_csv_to_usb(request):
        """Re-run the dive-data CSV export to USB for a past dive.

        Useful when the USB drive was missing at dive end, or when the
        operator wants to refresh the cached CSV.  Returns the same status
        dict the background exporter writes into the dive record.
        """
        dive_id = request.path_params.get("dive_id", "").strip()
        if not re.fullmatch(r"dive_\d{4}", dive_id):
            return Response(
                status_code=400,
                description=json.dumps({"error": "Invalid dive id"}),
                headers={"Content-Type": "application/json"},
            )
        path = DIVES_DIR / f"{dive_id}.json"
        if not path.is_file():
            return Response(
                status_code=404,
                description=json.dumps({"error": "Dive record not found"}),
                headers={"Content-Type": "application/json"},
            )
        try:
            result = await dive_csv_export.export_dive_csv_to_usb(path)
        except Exception as e:
            logger.exception("Manual CSV export failed for %s", dive_id)
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )
        return Response(
            status_code=200,
            description=json.dumps(result, default=str),
            headers={"Content-Type": "application/json"},
        )
