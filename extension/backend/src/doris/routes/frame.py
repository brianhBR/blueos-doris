"""Frame configuration routes.

Exposes endpoints to view, apply, and inspect the DORIS vehicle frame.
"""

import json
import logging

from robyn import Robyn

from ..services.frame import FrameService

logger = logging.getLogger(__name__)

frame_service = FrameService()


def register_frame_routes(app: Robyn) -> None:

    @app.get("/api/v1/frame")
    async def get_frame_definition():
        """Return the DORIS frame definition (parameters that will be applied)."""
        frame = frame_service.load_frame_definition()
        if frame is None:
            return json.dumps({"error": "Frame definition not found"})
        return json.dumps(frame)

    @app.get("/api/v1/frame/status")
    async def get_frame_status():
        """Read key parameters from the vehicle and report frame state."""
        try:
            status = await frame_service.get_frame_status()
            return json.dumps(status)
        except Exception as e:
            logger.error("Failed to get frame status: %s", e)
            return json.dumps({"error": str(e)})

    @app.get("/api/v1/frame/vehicle/params")
    async def get_vehicle_params():
        """Pull all current parameters from the connected vehicle."""
        try:
            params = await frame_service.fetch_vehicle_params()
            return json.dumps({
                "count": len(params),
                "parameters": params,
            })
        except Exception as e:
            logger.error("Failed to fetch vehicle params: %s", e)
            return json.dumps({"error": str(e)})

    @app.post("/api/v1/frame/apply")
    async def apply_frame(request):
        """Apply the DORIS frame to the connected vehicle.

        Optional JSON body: {"frame": "doris"}
        """
        try:
            body = json.loads(request.body) if request.body else {}
        except (json.JSONDecodeError, TypeError):
            body = {}

        frame_name = body.get("frame", "doris")
        try:
            result = await frame_service.apply_frame(frame_name)
            return json.dumps(result)
        except Exception as e:
            logger.error("Failed to apply frame: %s", e)
            return json.dumps({"success": False, "error": str(e)})
