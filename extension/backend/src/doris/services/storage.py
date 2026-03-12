"""Storage and file management service.

Scans the local filesystem for media files instead of using the
File Browser HTTP API. Paths are configurable via environment
variables, defaulting to the BlueOS data directory mounted into
the container.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from ..models.media import MediaFile, MediaMission, MediaType, SyncStatus

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset(("jpg", "jpeg", "png", "gif", "bmp", "tiff", "raw", "dng"))
VIDEO_EXTENSIONS = frozenset(("mp4", "avi", "mov", "mkv", "webm", "ts"))
DATA_EXTENSIONS = frozenset(("csv", "json", "bin", "log", "txt", "bag", "mcap", "lua"))
ALL_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | DATA_EXTENSIONS

DATA_ROOT = Path(os.environ.get("DORIS_DATA_ROOT", "/tmp/storage"))


def _detect_media_type(filename: str) -> MediaType:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in IMAGE_EXTENSIONS:
        return MediaType.IMAGE
    if ext in VIDEO_EXTENSIONS:
        return MediaType.VIDEO
    return MediaType.DATA


def _file_to_media(path: Path, root: Path) -> MediaFile:
    """Convert a filesystem path to a MediaFile model."""
    stat = path.stat()
    rel = path.relative_to(root)
    return MediaFile(
        id=str(rel),
        filename=path.name,
        media_type=_detect_media_type(path.name),
        size_bytes=stat.st_size,
        created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        mission_id=rel.parts[0] if len(rel.parts) > 1 else None,
        download_url=f"/api/v1/media/download/{rel}",
    )


class StorageService:
    """Service for managing stored media files on the local filesystem."""

    def __init__(self, root: Path | None = None):
        self.root = root or DATA_ROOT
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)

    async def get_media_files(
        self,
        mission_id: str | None = None,
        media_type: MediaType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MediaFile]:
        """List media files from the local data directory."""
        try:
            search_root = self.root / mission_id if mission_id else self.root
            if not search_root.exists():
                return []

            files: list[MediaFile] = []
            for path in search_root.rglob("*"):
                try:
                    if not path.is_file():
                        continue
                    ext = path.suffix.lstrip(".").lower()
                    if ext not in ALL_EXTENSIONS:
                        continue
                    mf = _file_to_media(path, self.root)
                    if media_type and mf.media_type != media_type:
                        continue
                    files.append(mf)
                except (FileNotFoundError, PermissionError):
                    continue

            files.sort(key=lambda f: f.created_at, reverse=True)

            return files[offset : offset + limit]

        except Exception as e:
            logger.warning(f"Failed to scan media files: {e}")
            raise

    async def get_missions_with_media(self) -> list[MediaMission]:
        """Discover missions by scanning top-level subdirectories."""
        try:
            if not self.root.exists():
                return []

            missions: list[MediaMission] = []
            for entry in sorted(self.root.iterdir(), reverse=True):
                if not entry.is_dir():
                    continue

                images = videos = data_files = 0
                total_size = 0
                latest_mtime = 0.0

                for path in entry.rglob("*"):
                    try:
                        if not path.is_file():
                            continue
                        ext = path.suffix.lstrip(".").lower()
                        if ext not in ALL_EXTENSIONS:
                            continue
                        stat = path.stat()
                        total_size += stat.st_size
                        latest_mtime = max(latest_mtime, stat.st_mtime)
                        mt = _detect_media_type(path.name)
                        if mt == MediaType.IMAGE:
                            images += 1
                        elif mt == MediaType.VIDEO:
                            videos += 1
                        else:
                            data_files += 1
                    except (FileNotFoundError, PermissionError):
                        continue

                if images + videos + data_files == 0:
                    continue

                missions.append(
                    MediaMission(
                        mission_id=entry.name,
                        mission_name=entry.name,
                        date=datetime.fromtimestamp(latest_mtime, tz=timezone.utc) if latest_mtime else datetime.now(tz=timezone.utc),
                        image_count=images,
                        video_count=videos,
                        data_file_count=data_files,
                        total_size_bytes=total_size,
                    )
                )

            return missions

        except Exception as e:
            logger.warning(f"Failed to scan missions: {e}")
            raise

    async def get_file(self, file_path: str) -> bytes | None:
        """Read a file from disk."""
        try:
            full = self.root / file_path
            if not full.is_file() or not full.resolve().is_relative_to(self.root.resolve()):
                return None
            return full.read_bytes()
        except Exception as e:
            logger.warning(f"Failed to read file '{file_path}': {e}")
            return None

    async def delete_file(self, file_path: str) -> bool:
        """Delete a file from disk."""
        try:
            full = self.root / file_path
            if not full.resolve().is_relative_to(self.root.resolve()):
                raise ValueError("Path traversal denied")
            full.unlink(missing_ok=True)
            return True
        except Exception as e:
            logger.warning(f"Failed to delete file '{file_path}': {e}")
            raise

    async def get_sync_status(self) -> SyncStatus:
        """Get sync status (placeholder)."""
        return SyncStatus(
            is_syncing=False,
            pending_files=0,
            synced_files=0,
            total_files=0,
        )

    async def start_sync(self) -> bool:
        """Start sync (placeholder)."""
        return True

    async def close(self) -> None:
        """Nothing to close for filesystem access."""
