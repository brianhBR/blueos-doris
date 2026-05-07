"""HTTP endpoints for onboard IP camera recording.

The same handlers are exposed under two URL prefixes:

* ``/rec/*`` - used by the ``doris.lua`` dive script (fire-and-forget
  POSTs from a raw socket).  Short paths keep the Lua HTTP emitter
  compact.
* ``/api/v1/ipcam/record/*`` - used by the web UI.

Endpoints:

* ``POST /rec/start?phase=<label>&split_duration=<seconds>`` - start
  (or re-use) a recording session.  ``phase`` defaults to ``manual``
  for UI-initiated recordings.  Idempotent when already recording.
* ``POST /rec/stop`` - finalise the session cleanly.
* ``POST /rec/rotate?phase=<label>`` - zero-gap phase rotation.
  splitmuxsink fires ``split-now`` on the next keyframe; the next
  ``.ts`` segment is tagged with ``phase`` in its filename.  The
  RTSP/muxer pipeline stays alive across the split.
* ``GET /rec/status`` - read-only inspection.
"""

import json
import logging

from robyn import Response, Robyn

from ..services import ip_camera_recorder as iprec

logger = logging.getLogger(__name__)


def _json_response(result: dict, status_code: int = 200) -> Response:
    return Response(
        status_code=status_code,
        description=json.dumps(result),
        headers={"Content-Type": "application/json"},
    )


async def _recorder_start_core(request):
    phase_raw = request.query_params.get("phase", None)
    phase = phase_raw if phase_raw not in (None, "") else None
    # ``split_duration`` is the historical name used by /rec/start;
    # ``segment_seconds`` is accepted as a synonym so callers wired to
    # /rec/rotate (which advertises both) can use the same key on both
    # endpoints.
    raw = (
        request.query_params.get("split_duration", "")
        or request.query_params.get("segment_seconds", "")
    )
    seg: int | None = None
    if raw not in (None, ""):
        try:
            seg = int(raw)
        except (TypeError, ValueError):
            seg = None
    logger.info("RECORD /start called (phase=%s, split_duration=%s)", phase, seg)
    try:
        result = await iprec.start_recording(segment_seconds=seg, phase=phase)
    except Exception as e:
        logger.exception("recorder /start failed")
        return _json_response({"success": False, "message": str(e)}, 500)
    code = 200 if result.get("success") else 400
    return _json_response(result, code)


async def _recorder_stop_core(_request):
    logger.warning("RECORD /stop called")
    try:
        result = await iprec.stop_recording()
    except Exception as e:
        logger.exception("recorder /stop failed")
        return _json_response({"success": False, "message": str(e)}, 500)
    return _json_response(result)


async def _recorder_snapshot_core(request):
    phase_raw = request.query_params.get("phase", "")
    if phase_raw in (None, ""):
        logger.warning("RECORD /snapshot missing required ?phase=...")
        return _json_response(
            {"success": False, "reason": "missing_phase",
             "message": "phase query parameter is required"},
            400,
        )
    logger.info("RECORD /snapshot called (phase=%s)", phase_raw)
    try:
        result = await iprec.take_phase_snapshot(phase_raw)
    except Exception as e:
        logger.exception("recorder /snapshot failed")
        return _json_response({"success": False, "message": str(e)}, 500)
    code = 200
    if not result.get("success"):
        # Pop the hint so it doesn't leak into the body; fall back to 400.
        code = result.pop("http_status", 400)
    return _json_response(result, code)


async def _recorder_rotate_core(request):
    phase_raw = request.query_params.get("phase", "")
    if phase_raw in (None, ""):
        logger.warning("RECORD /rotate missing required ?phase=...")
        return _json_response(
            {"success": False, "reason": "missing_phase",
             "message": "phase query parameter is required"},
            400,
        )
    # Optional segment override (seconds).  Used by the Lua dive script
    # to switch the live splitmuxsink to 5-min chunks on the
    # descent->bottom rotation in CONTINUOUS mode and back to the
    # default on the bottom->ascent rotation.  Accepted under either
    # name to mirror /rec/start (which uses ``split_duration``).
    seg: int | None = None
    raw_seg = (
        request.query_params.get("split_duration", "")
        or request.query_params.get("segment_seconds", "")
    )
    if raw_seg not in (None, ""):
        try:
            seg = int(raw_seg)
        except (TypeError, ValueError):
            seg = None
    logger.info(
        "RECORD /rotate called (phase=%s, split_duration=%s)",
        phase_raw, seg,
    )
    try:
        result = await iprec.rotate_to_phase(phase_raw, segment_seconds=seg)
    except Exception as e:
        logger.exception("recorder /rotate failed")
        return _json_response({"success": False, "message": str(e)}, 500)
    # Not-recording is reported as 200 with success=false rather than an
    # HTTP error; this is a fire-and-forget endpoint for the Lua script
    # and we don't want 4xx responses polluting doris.log.
    return _json_response(result)


async def _recorder_status_core(_request):
    try:
        result = await iprec.recording_status()
    except Exception as e:
        logger.exception("recorder /status failed")
        return _json_response({"success": False, "message": str(e)}, 500)
    return _json_response({"success": True, **result})


def register_recorder_routes(app: Robyn) -> None:
    """Register POST /rec/* (Lua) and /api/v1/ipcam/record/* (web UI).

    POST prevents BlueOS helper service-discovery GETs from triggering
    start/stop as a side effect.  Status remains GET (read-only).
    """

    @app.post("/rec/start")
    async def recorder_start_lua(request):
        return await _recorder_start_core(request)

    @app.post("/api/v1/ipcam/record/start")
    async def recorder_start_api(request):
        return await _recorder_start_core(request)

    @app.post("/rec/stop")
    async def recorder_stop_lua(request):
        return await _recorder_stop_core(request)

    @app.post("/api/v1/ipcam/record/stop")
    async def recorder_stop_api(request):
        return await _recorder_stop_core(request)

    @app.post("/rec/rotate")
    async def recorder_rotate_lua(request):
        return await _recorder_rotate_core(request)

    @app.post("/api/v1/ipcam/record/rotate")
    async def recorder_rotate_api(request):
        return await _recorder_rotate_core(request)

    @app.post("/rec/snapshot")
    async def recorder_snapshot_lua(request):
        return await _recorder_snapshot_core(request)

    @app.post("/api/v1/ipcam/record/snapshot")
    async def recorder_snapshot_api(request):
        return await _recorder_snapshot_core(request)

    @app.get("/rec/status")
    async def recorder_status_lua(request):
        return await _recorder_status_core(request)

    @app.get("/api/v1/ipcam/record/status")
    async def recorder_status_api(request):
        return await _recorder_status_core(request)
