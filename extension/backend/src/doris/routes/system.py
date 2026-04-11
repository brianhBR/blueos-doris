"""System API routes."""

import json
import logging
from datetime import datetime, timezone

from robyn import Response, Robyn

from ..services.external_storage import get_migration_status
from ..services.system import SystemService
from ..services.timesync import timesync_service

logger = logging.getLogger(__name__)


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

    @app.get("/api/v1/system/storage/migration")
    async def get_storage_migration(request):
        """Get external-storage migration status."""
        return json.dumps(get_migration_status())

    @app.get("/api/v1/system/time")
    async def get_system_time(request):
        """Return current system clock, sync status, and source."""
        return json.dumps(timesync_service.status())

    @app.post("/api/v1/system/time")
    async def set_system_time(request):
        """Accept a trusted UTC timestamp from the client browser as a
        fallback when the Artemis GPS time is not available.

        Body: {"utc": "2026-04-10T20:00:00Z"} or {"unix": 1781280000}
        """
        try:
            body = json.loads(request.body) if request.body else {}
        except (json.JSONDecodeError, TypeError):
            return Response(
                status_code=400,
                description=json.dumps({"error": "Invalid JSON"}),
                headers={"Content-Type": "application/json"},
            )

        client_dt: datetime | None = None
        if "utc" in body:
            raw = str(body["utc"]).replace("Z", "+00:00")
            try:
                client_dt = datetime.fromisoformat(raw)
            except ValueError:
                pass
        elif "unix" in body:
            try:
                client_dt = datetime.fromtimestamp(float(body["unix"]), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                pass

        if client_dt is None:
            return Response(
                status_code=400,
                description=json.dumps({"error": "Provide 'utc' (ISO) or 'unix' (epoch)"}),
                headers={"Content-Type": "application/json"},
            )

        result = await timesync_service.try_sync_from_client(client_dt)
        return json.dumps(result)

    @app.get("/api/v1/health")
    async def health_check(request):
        """Health check endpoint."""
        return json.dumps({"status": "healthy", "service": "doris-backend"})
