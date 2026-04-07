"""Storage and file management service.

Scans the local filesystem for media files instead of using the
File Browser HTTP API. Paths are configurable via environment
variables, defaulting to the BlueOS data directory mounted into
the container.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models.configuration import (
    ConfigurationSummary,
    DeploymentConfiguration,
)
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


def _parse_datetime_from_filename(filename: str) -> datetime | None:
    """Infer capture time from common DORIS / recorder filename patterns."""
    # recorder_20260404_074153.mcap
    m = re.search(r"recorder_(\d{8})_(\d{6})(?=[_.]|\b)", filename, re.IGNORECASE)
    if m:
        d, t = m.group(1), m.group(2)
        try:
            return datetime(
                int(d[:4]),
                int(d[4:6]),
                int(d[6:8]),
                int(t[:2]),
                int(t[2:4]),
                int(t[4:6]),
                tzinfo=timezone.utc,
            )
        except ValueError:
            pass
    # Loose YYYYMMDD_HHMMSS anywhere in stem
    m2 = re.search(r"(20\d{2})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})", filename)
    if m2:
        try:
            return datetime(
                int(m2.group(1)),
                int(m2.group(2)),
                int(m2.group(3)),
                int(m2.group(4)),
                int(m2.group(5)),
                int(m2.group(6)),
                tzinfo=timezone.utc,
            )
        except ValueError:
            pass
    return None


def _effective_created_at(path: Path, mtime_ts: float) -> datetime:
    """Use mtime when plausible; otherwise parse filename or clamp bogus mtimes.

    Some mounts (or flight-controller exports) report impossible mtimes (e.g. year 2073).
    Recorder files embed the real time in the name.
    """
    now = datetime.now(timezone.utc)
    lo = datetime(2000, 1, 1, tzinfo=timezone.utc)
    hi = now + timedelta(days=2)
    mtime_dt = datetime.fromtimestamp(mtime_ts, tz=timezone.utc)

    if lo <= mtime_dt <= hi:
        return mtime_dt

    parsed = _parse_datetime_from_filename(path.name)
    if parsed is not None and lo <= parsed <= hi + timedelta(days=7):
        return parsed

    # No filename hint: clamp insane mtimes so the Data page stays usable
    if mtime_dt > hi:
        return hi
    if mtime_dt < lo:
        return lo
    return mtime_dt


def _file_to_media(path: Path, root: Path) -> MediaFile:
    """Convert a filesystem path to a MediaFile model."""
    stat = path.stat()
    rel = path.relative_to(root)
    return MediaFile(
        id=str(rel),
        filename=path.name,
        media_type=_detect_media_type(path.name),
        size_bytes=stat.st_size,
        created_at=_effective_created_at(path, stat.st_mtime),
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
                latest_date: datetime | None = None

                for path in entry.rglob("*"):
                    try:
                        if not path.is_file():
                            continue
                        ext = path.suffix.lstrip(".").lower()
                        if ext not in ALL_EXTENSIONS:
                            continue
                        stat = path.stat()
                        total_size += stat.st_size
                        eff = _effective_created_at(path, stat.st_mtime)
                        if latest_date is None or eff > latest_date:
                            latest_date = eff
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
                        date=latest_date or datetime.now(tz=timezone.utc),
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

    # ── Configuration management ────────────────────────────────

    @property
    def _config_dir(self) -> Path:
        d = self.root / "configurations"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _slug(name: str) -> str:
        """Turn a human-readable name into a safe filename stem."""
        slug = re.sub(r"[^\w\s-]", "", name.strip().lower())
        slug = re.sub(r"[\s-]+", "_", slug)
        return slug or "unnamed"

    async def save_configuration(self, config: DeploymentConfiguration) -> DeploymentConfiguration:
        """Persist a configuration as JSON. Overwrites if name already exists."""
        config.updated_at = datetime.now()
        path = self._config_dir / f"{self._slug(config.name)}.json"
        path.write_text(config.model_dump_json(indent=2))
        logger.info(f"Configuration saved: {config.name} -> {path}")
        return config

    async def load_configuration(self, name: str) -> DeploymentConfiguration | None:
        """Load a configuration by name."""
        path = self._config_dir / f"{self._slug(name)}.json"
        if not path.is_file():
            return None
        try:
            return DeploymentConfiguration.model_validate_json(path.read_text())
        except Exception as e:
            logger.warning(f"Failed to parse configuration '{name}': {e}")
            return None

    async def list_configurations(self) -> list[ConfigurationSummary]:
        """Return a summary list of all saved configurations."""
        summaries: list[ConfigurationSummary] = []
        for path in sorted(self._config_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                summaries.append(
                    ConfigurationSummary(
                        name=data["name"],
                        created_at=data.get("created_at", datetime.now().isoformat()),
                        updated_at=data.get("updated_at", datetime.now().isoformat()),
                    )
                )
            except Exception as e:
                logger.warning(f"Skipping invalid config file {path.name}: {e}")
        return summaries

    async def delete_configuration(self, name: str) -> bool:
        """Delete a configuration by name."""
        path = self._config_dir / f"{self._slug(name)}.json"
        if not path.is_file():
            return False
        path.unlink()
        logger.info(f"Configuration deleted: {name}")
        return True

    async def close(self) -> None:
        """Nothing to close for filesystem access."""
