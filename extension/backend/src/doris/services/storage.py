"""Storage and file management service."""

import logging
from datetime import datetime

from ..config import blueos_services
from ..models.media import MediaFile, MediaMission, MediaType, SyncStatus
from .base import BlueOSClient

logger = logging.getLogger(__name__)


class StorageService:
    """Service for managing stored media and files."""

    _last_files: list[MediaFile] | None = None
    _last_missions: list[MediaMission] | None = None

    def __init__(self):
        self.file_browser = BlueOSClient(blueos_services.file_browser)
        self.recorder = BlueOSClient(blueos_services.recorder_extractor)

    async def get_media_files(
        self,
        mission_id: str | None = None,
        media_type: MediaType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MediaFile]:
        """Get list of media files from BlueOS File Browser.

        Raises on failure if no cached data is available.
        """
        try:
            params: dict = {"limit": limit, "offset": offset}
            if mission_id:
                params["mission_id"] = mission_id

            files_data = await self.file_browser.get("/api/resources", params=params)

            files = []
            for file_info in files_data.get("items", []):
                file_type = self._detect_media_type(file_info.get("name", ""))
                if media_type and file_type != media_type:
                    continue

                files.append(
                    MediaFile(
                        id=file_info.get("id", file_info.get("path", "")),
                        filename=file_info.get("name", ""),
                        media_type=file_type,
                        size_bytes=file_info.get("size", 0),
                        duration_seconds=file_info.get("duration"),
                        resolution=file_info.get("resolution"),
                        created_at=datetime.fromisoformat(
                            file_info.get("modified", datetime.now().isoformat())
                        ),
                        mission_id=file_info.get("mission_id"),
                        thumbnail_url=file_info.get("thumbnail"),
                        download_url=f"/api/files/{file_info.get('path', '')}",
                        is_synced=file_info.get("synced", False),
                    )
                )

            StorageService._last_files = files
            return files

        except Exception as e:
            logger.warning(f"Failed to get media files: {type(e).__name__}: {e}")
            if StorageService._last_files is not None:
                logger.info("Using cached media files")
                return StorageService._last_files
            raise

    async def get_missions_with_media(self) -> list[MediaMission]:
        """Get list of missions that have media.

        Raises on failure if no cached data is available.
        """
        try:
            missions_data = await self.recorder.get("/v1.0/recordings")

            missions = []
            for mission in missions_data:
                missions.append(
                    MediaMission(
                        mission_id=mission.get("id", ""),
                        mission_name=mission.get("name", "Unknown Mission"),
                        date=datetime.fromisoformat(
                            mission.get("date", datetime.now().isoformat())
                        ),
                        image_count=mission.get("image_count", 0),
                        video_count=mission.get("video_count", 0),
                        data_file_count=mission.get("data_count", 0),
                        total_size_bytes=mission.get("total_size", 0),
                        thumbnail_url=mission.get("thumbnail"),
                    )
                )

            StorageService._last_missions = missions
            return missions

        except Exception as e:
            logger.warning(
                f"Failed to get missions with media: {type(e).__name__}: {e}"
            )
            if StorageService._last_missions is not None:
                logger.info("Using cached media missions")
                return StorageService._last_missions
            raise

    async def get_file(self, file_path: str) -> bytes | None:
        """Download a file."""
        try:
            response = await self.file_browser.client.get(f"/api/raw/{file_path}")
            response.raise_for_status()
            return response.content
        except Exception as e:
            logger.warning(f"Failed to download file '{file_path}': {e}")
            return None

    async def delete_file(self, file_path: str) -> bool:
        """Delete a file."""
        try:
            await self.file_browser.delete(f"/api/resources/{file_path}")
            return True
        except Exception as e:
            logger.warning(f"Failed to delete file '{file_path}': {e}")
            raise

    async def get_sync_status(self) -> SyncStatus:
        """Get cloud sync status."""
        return SyncStatus(
            is_syncing=False,
            pending_files=0,
            synced_files=0,
            total_files=0,
        )

    async def start_sync(self) -> bool:
        """Start cloud sync."""
        return True

    def _detect_media_type(self, filename: str) -> MediaType:
        """Detect media type from filename extension."""
        ext = filename.lower().split(".")[-1] if "." in filename else ""

        if ext in ("jpg", "jpeg", "png", "gif", "bmp", "tiff", "raw"):
            return MediaType.IMAGE
        elif ext in ("mp4", "avi", "mov", "mkv", "webm"):
            return MediaType.VIDEO
        else:
            return MediaType.DATA

    async def close(self) -> None:
        """Close HTTP clients."""
        await self.file_browser.close()
        await self.recorder.close()
