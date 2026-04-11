"""HTTP endpoints for onboard IP camera recording (Lua-compatible /start /stop)."""

import json
import logging

from robyn import Response, Robyn

from ..services import ip_camera_recorder as iprec

logger = logging.getLogger(__name__)


async def _recorder_start_core(request):
    try:
        raw = request.query_params.get("split_duration", "")
        seg = None
        if raw not in (None, ""):
            seg = int(raw)
    except (TypeError, ValueError):
        seg = None
    try:
        result = await iprec.start_recording(segment_seconds=seg)
    except Exception as e:
        logger.exception("recorder /start failed")
        return Response(
            status_code=500,
            description=json.dumps({"success": False, "message": str(e)}),
            headers={"Content-Type": "application/json"},
        )
    code = 200 if result.get("success") else 400
    return Response(
        status_code=code,
        description=json.dumps(result),
        headers={"Content-Type": "application/json"},
    )


async def _recorder_stop_core(_request):
    try:
        result = await iprec.stop_recording()
    except Exception as e:
        logger.exception("recorder /stop failed")
        return Response(
            status_code=500,
            description=json.dumps({"success": False, "message": str(e)}),
            headers={"Content-Type": "application/json"},
        )
    return Response(
        status_code=200,
        description=json.dumps(result),
        headers={"Content-Type": "application/json"},
    )


async def _recorder_status_core(_request):
    try:
        result = await iprec.recording_status()
    except Exception as e:
        logger.exception("recorder /status failed")
        return Response(
            status_code=500,
            description=json.dumps({"success": False, "message": str(e)}),
            headers={"Content-Type": "application/json"},
        )
    return Response(
        status_code=200,
        description=json.dumps({"success": True, **result}),
        headers={"Content-Type": "application/json"},
    )


def register_recorder_routes(app: Robyn) -> None:
    """Register GET /start, /stop, /status (Lua) and /api/v1/ipcam/record/* (web UI)."""

    @app.get("/start")
    async def recorder_start_lua(request):
        return await _recorder_start_core(request)

    @app.get("/api/v1/ipcam/record/start")
    async def recorder_start_api(request):
        return await _recorder_start_core(request)

    @app.get("/stop")
    async def recorder_stop_lua(request):
        return await _recorder_stop_core(request)

    @app.get("/api/v1/ipcam/record/stop")
    async def recorder_stop_api(request):
        return await _recorder_stop_core(request)

    @app.get("/status")
    async def recorder_status_lua(request):
        return await _recorder_status_core(request)

    @app.get("/api/v1/ipcam/record/status")
    async def recorder_status_api(request):
        return await _recorder_status_core(request)
