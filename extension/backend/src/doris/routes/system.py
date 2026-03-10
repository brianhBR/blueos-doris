"""System API routes."""

import json

from robyn import Response, Robyn

from ..services.system import SystemService


def register_system_routes(app: Robyn) -> None:
    """Register system-related API routes."""

    system_service = SystemService()

    @app.get("/api/v1/system/status")
    async def get_system_status(request):
        """Get complete system status including battery, storage, and location."""
        try:
            status = await system_service.get_system_status()
            return json.dumps(status.model_dump(mode="json"))
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/system/battery")
    async def get_battery_info(request):
        """Get battery information."""
        try:
            battery = await system_service.get_battery_info()
            return json.dumps(battery.model_dump(mode="json"))
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/system/storage")
    async def get_storage_info(request):
        """Get storage information."""
        try:
            storage = await system_service.get_storage_info()
            return json.dumps(storage.model_dump(mode="json"))
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/system/location")
    async def get_location(request):
        """Get GPS location information."""
        try:
            location = await system_service.get_location()
            if location:
                return json.dumps(location.model_dump(mode="json"))
            return Response(
                status_code=404,
                description=json.dumps({"error": "Location not available"}),
                headers={"Content-Type": "application/json"},
            )
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/health")
    async def health_check(request):
        """Health check endpoint."""
        return json.dumps({"status": "healthy", "service": "doris-backend"})
