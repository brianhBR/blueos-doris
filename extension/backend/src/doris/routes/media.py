"""Media API routes."""

import json

from robyn import Response, Robyn

from ..models.media import MediaType
from ..services.storage import StorageService


def register_media_routes(app: Robyn) -> None:
    """Register media-related API routes."""

    storage_service = StorageService()

    @app.get("/api/v1/media/files")
    async def get_media_files(request):
        """Get list of media files with optional filtering."""
        try:
            mission_id = request.query_params.get("mission_id")
            media_type_str = request.query_params.get("type")
            limit = int(request.query_params.get("limit", "50"))
            offset = int(request.query_params.get("offset", "0"))

            media_type = None
            if media_type_str:
                media_type = MediaType(media_type_str)

            files = await storage_service.get_media_files(
                mission_id=mission_id,
                media_type=media_type,
                limit=limit,
                offset=offset,
            )

            return json.dumps([f.model_dump(mode="json") for f in files])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/media/missions")
    async def get_missions_with_media(request):
        """Get list of missions that have media."""
        try:
            missions = await storage_service.get_missions_with_media()
            return json.dumps([m.model_dump(mode="json") for m in missions])
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.get("/api/v1/media/sync/status")
    async def get_sync_status(request):
        """Get cloud sync status."""
        try:
            status = await storage_service.get_sync_status()
            return json.dumps(status.model_dump(mode="json"))
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.post("/api/v1/media/sync/start")
    async def start_sync(request):
        """Start cloud sync."""
        try:
            success = await storage_service.start_sync()
            return json.dumps({"success": success})
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

    @app.delete("/api/v1/media/files/:file_id")
    async def delete_file(request):
        """Delete a media file."""
        try:
            file_id = request.path_params.get("file_id")
            success = await storage_service.delete_file(file_id)
            return json.dumps({"success": success})
        except Exception as e:
            return Response(
                status_code=500,
                description=json.dumps({"error": str(e)}),
                headers={"Content-Type": "application/json"},
            )

