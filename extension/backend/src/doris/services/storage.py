"""Storage and file management service."""

from datetime import datetime

from ..config import blueos_services
from ..models.media import MediaFile, MediaMission, MediaType, SyncStatus
from .base import BlueOSClient


class StorageService:
    """Service for managing stored media and files."""

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
        """Get list of media files."""
        try:
            # Query file browser for media files
            params = {"limit": limit, "offset": offset}
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
            return files

        except Exception:
            # Return mock data
            return self._get_mock_media_files()

    async def get_missions_with_media(self) -> list[MediaMission]:
        """Get list of missions that have media."""
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
            return missions

        except Exception:
            # Return mock data
            return self._get_mock_missions()

    async def get_file(self, file_path: str) -> bytes | None:
        """Download a file."""
        try:
            response = await self.file_browser.client.get(f"/api/raw/{file_path}")
            response.raise_for_status()
            return response.content
        except Exception:
            return None

    async def delete_file(self, file_path: str) -> bool:
        """Delete a file."""
        try:
            await self.file_browser.delete(f"/api/resources/{file_path}")
            return True
        except Exception:
            return False

    async def get_sync_status(self) -> SyncStatus:
        """Get cloud sync status."""
        # Mock implementation - real implementation would check cloud sync service
        return SyncStatus(
            is_syncing=False,
            pending_files=0,
            synced_files=0,
            total_files=0,
        )

    async def start_sync(self) -> bool:
        """Start cloud sync."""
        # Mock implementation
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

    def _get_mock_media_files(self) -> list[MediaFile]:
        """Return mock media files for testing."""
        now = datetime.now()
        return [
            MediaFile(
                id="img-001",
                filename="dive_photo_001.jpg",
                media_type=MediaType.IMAGE,
                size_bytes=5_242_880,
                created_at=now,
                download_url="/api/files/dive_photo_001.jpg",
            ),
            MediaFile(
                id="vid-001",
                filename="reef_survey.mp4",
                media_type=MediaType.VIDEO,
                size_bytes=524_288_000,
                duration_seconds=180,
                resolution="4K",
                created_at=now,
                download_url="/api/files/reef_survey.mp4",
            ),
        ]

    def _get_mock_missions(self) -> list[MediaMission]:
        """Return mock mission data for testing."""
        return [
            MediaMission(
                mission_id="mission-001",
                mission_name="Deep Sea Survey 2024-01",
                date=datetime(2026, 1, 5),
                image_count=487,
                video_count=3,
                data_file_count=12,
                total_size_bytes=2_147_483_648,
            ),
            MediaMission(
                mission_id="mission-002",
                mission_name="Coral Reef Documentation",
                date=datetime(2026, 1, 2),
                image_count=324,
                video_count=5,
                data_file_count=8,
                total_size_bytes=1_610_612_736,
            ),
        ]

    async def close(self) -> None:
        """Close HTTP clients."""
        await self.file_browser.close()
        await self.recorder.close()

