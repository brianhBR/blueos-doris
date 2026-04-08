"""Sensor API routes."""

import json

from robyn import Response, Robyn

from ..models.sensors import SensorConfig
from ..services.camera import CameraService
from ..services.sensors import SensorService


def register_sensor_routes(app: Robyn) -> None:
    """Register sensor-related API routes."""

    sensor_service = SensorService()
    camera_service = CameraService()

    @app.get("/api/v1/sensors/modules")
    async def get_connected_modules(request):
        """Get all connected modules (cameras, sensors, lights)."""
        try:
            modules = await sensor_service.get_connected_modules()
            return json.dumps([m.model_dump(mode="json") for m in modules])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/sensors/streams")
    async def get_video_streams(request):
        """Get all available video streams from the Camera Manager."""
        try:
            streams = await sensor_service.get_video_streams()
            return json.dumps([s.model_dump(mode="json") for s in streams])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/camera/snapshot")
    async def camera_snapshot(request):
        """Proxy a JPEG snapshot from the Camera Manager for the sensor page preview."""
        source = request.query_params.get("source", None)
        try:
            data = await camera_service.get_snapshot(source=source)
        except Exception:
            data = None
        if data is None:
            return Response(
                status_code=502,
                description=json.dumps({"error": "No snapshot available from camera"}),
                headers={"Content-Type": "application/json"},
            )
        return Response(
            status_code=200,
            description=data,
            headers={
                "Content-Type": "image/jpeg",
                "Cache-Control": "no-cache",
                "Content-Length": str(len(data)),
            },
        )

    @app.get("/api/v1/sensors/:sensor_id/readings")
    async def get_sensor_readings(request):
        """Get recent readings from a specific sensor."""
        try:
            sensor_id = request.path_params.get("sensor_id")
            readings = await sensor_service.get_sensor_readings(sensor_id)
            return json.dumps([r.model_dump(mode="json") for r in readings])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.put("/api/v1/sensors/:sensor_id/config")
    async def configure_sensor(request):
        """Update sensor configuration."""
        try:
            sensor_id = request.path_params.get("sensor_id")
            data = json.loads(request.body)

            config = SensorConfig(
                sensor_id=sensor_id,
                sample_rate=data.get("sample_rate", 1.0),
                enabled=data.get("enabled", True),
                calibration_file=data.get("calibration_file"),
            )

            success = await sensor_service.configure_sensor(config)
            return json.dumps({"success": success})
        except json.JSONDecodeError:
            return Response(
                status_code=400,
                description=json.dumps({"error": "Invalid JSON"}),
                headers={"Content-Type": "application/json"},
            )
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )
