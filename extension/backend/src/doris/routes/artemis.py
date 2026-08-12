"""Artemis SVL firmware flashing routes.

Provides REST endpoints for serial port listing, firmware upload,
starting a flash operation, and polling flash progress.
"""

import json
import logging

from robyn import Response, Robyn

from ..services.artemis import ArtemisService
from ..services.shutdown_policy import get_shutdown_policy, set_bench_mode

logger = logging.getLogger(__name__)


def register_artemis_routes(app: Robyn) -> None:
    """Register Artemis-related API routes."""

    artemis_service = ArtemisService()

    @app.get("/api/v1/artemis/shutdown-policy")
    async def get_agt_shutdown_policy(request):
        """Return the persistent bench override for payload shutdown."""
        return json.dumps(get_shutdown_policy())

    @app.put("/api/v1/artemis/shutdown-policy")
    async def update_agt_shutdown_policy(request):
        """Persist whether automatic payload cutoff is disabled for bench use."""
        try:
            data = request.json()
        except Exception:
            return Response(
                status_code=400,
                description=json.dumps({"error": "Invalid JSON body"}),
                headers={"Content-Type": "application/json"},
            )
        bench_mode = data.get("bench_mode") if isinstance(data, dict) else None
        if not isinstance(bench_mode, bool):
            return Response(
                status_code=400,
                description=json.dumps({"error": "'bench_mode' must be a boolean"}),
                headers={"Content-Type": "application/json"},
            )
        try:
            return json.dumps(set_bench_mode(bench_mode))
        except OSError as error:
            logger.exception("Failed to persist AGT shutdown policy")
            return Response(
                status_code=500,
                description=json.dumps({"error": str(error)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/artemis/ports")
    async def list_ports(request):
        """List available serial ports."""
        try:
            ports = artemis_service.list_serial_ports()
            return json.dumps([p.model_dump(mode="json") for p in ports])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.post("/api/v1/artemis/firmware/upload")
    async def upload_firmware(request):
        """Accept a firmware binary file upload."""
        try:
            filename = request.query_params.get("filename", "firmware.bin")

            body = request.body
            if not body:
                logger.warning("Firmware upload rejected: empty request body")
                return Response(
                    status_code=400,
                    description=json.dumps({"error": "Empty request body"}),
                    headers={"Content-Type": "application/json"},
                )

            if isinstance(body, str):
                body = body.encode("latin-1")

            logger.info("Receiving firmware upload: %s (%d bytes)", filename, len(body))
            path, size_bytes = artemis_service.save_firmware(filename, body)
            logger.info("Firmware saved to %s (%d bytes)", path, size_bytes)
            return json.dumps({"path": path, "size_bytes": size_bytes})
        except Exception as e:
            logger.exception("Firmware upload failed")
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.post("/api/v1/artemis/flash")
    async def start_flash(request):
        """Start a flash operation. Returns a session_id for polling progress."""
        try:
            data = request.json()
        except Exception:
            return Response(
                status_code=400,
                description=json.dumps({"error": "Invalid JSON body"}),
                headers={"Content-Type": "application/json"},
            )

        port = data.get("port")
        firmware_path = data.get("firmware_path")
        if not port or not firmware_path:
            return Response(
                status_code=400,
                description=json.dumps({"error": "Missing 'port' or 'firmware_path'"}),
                headers={"Content-Type": "application/json"},
            )

        baud = data.get("baud", 115200)
        timeout = data.get("timeout", 0.5)

        logger.info("Starting flash: port=%s firmware=%s baud=%d", port, firmware_path, baud)
        session_id = artemis_service.start_flash(
            port=port, firmware_path=firmware_path, baud=baud, timeout=timeout
        )
        logger.info("Flash session created: %s", session_id)
        return json.dumps({"session_id": session_id})

    @app.get("/api/v1/artemis/flash/status")
    async def flash_status(request):
        """Poll flash progress. Query param: session_id, from_line (optional)."""
        session_id = request.query_params.get("session_id", "")
        if not session_id:
            return Response(
                status_code=400,
                description=json.dumps({"error": "Missing 'session_id' query parameter"}),
                headers={"Content-Type": "application/json"},
            )

        session = artemis_service.get_session(session_id)
        if not session:
            return Response(
                status_code=404,
                description=json.dumps({"error": "Session not found"}),
                headers={"Content-Type": "application/json"},
            )

        from_line = int(request.query_params.get("from_line", "0") or "0")
        new_lines = session.lines[from_line:]

        return json.dumps({
            "session_id": session.session_id,
            "lines": new_lines,
            "total_lines": len(session.lines),
            "done": session.done,
            "success": session.success,
            "error": session.error,
        })
