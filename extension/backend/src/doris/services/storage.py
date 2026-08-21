"""Storage and file management service.

Scans the local filesystem for media files instead of using the
File Browser HTTP API. Paths are configurable via environment
variables, defaulting to the BlueOS data directory mounted into
the container.
"""

import json
import logging
import math
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from ..config import settings
from ..models.configuration import (
    ConfigurationSummary,
    DeploymentConfiguration,
)
from ..models.dive_history import DiveHistoryEntry
from ..models.media import MediaFile, MediaMission, MediaType, SyncStatus

from .binlog import slug_for_dive
from .usb_storage import iter_media_files_on_usb, iter_media_scan_roots

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset(("jpg", "jpeg", "png", "gif", "bmp", "tiff", "raw", "dng"))
VIDEO_EXTENSIONS = frozenset(("mp4", "avi", "mov", "mkv", "webm", "ts"))
DATA_EXTENSIONS = frozenset(
    ("csv", "json", "ndjson", "bin", "log", "txt", "bag", "mcap", "lua")
)
ALL_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | DATA_EXTENSIONS

DATA_ROOT = Path(os.environ.get("DORIS_DATA_ROOT", "/tmp/storage"))
RECORDER_ROOT = Path(os.environ.get("DORIS_RECORDER_ROOT", "/tmp/storage/userdata/recorder"))
# Internal fallback for IP-camera recordings when no USB stick is mounted.
# The recorder writes here (see services/ip_camera_recorder._output_dir);
# without scanning it, test/dive recordings made without a USB drive never
# appeared on the data page (issue #32).
IPCAM_ROOT = DATA_ROOT / settings.ipcam_recordings_subdir.strip("/")
# Path parts of the ipcam subdir relative to DATA_ROOT, e.g.
# ("userdata", "ipcam_recordings"). Used to label internal recordings by
# their per-dive folder instead of the generic "userdata" top-level dir.
IPCAM_SUBDIR_PARTS = tuple(Path(settings.ipcam_recordings_subdir.strip("/")).parts)

# BlueOS bind-mount folders under DATA_ROOT — not user dive names.
SYSTEM_TOP_LEVEL = frozenset(
    {"configurations", "notifications", "nginx", "dives"},
)
RECORDER_DIR = "recorder"

# External USB files use this prefix on ``MediaFile.id`` / download ``path=``.
USB_MEDIA_PREFIX = "usb:"


def media_download_id_from_abs_path(path: Path, data_root: Path) -> str:
    """Stable id for ``/api/v1/media/download`` (internal or USB)."""
    pr = path.resolve()
    dr = data_root.resolve()
    try:
        return str(pr.relative_to(dr)).replace("\\", "/")
    except ValueError:
        pass
    for mount_key, base in iter_media_scan_roots():
        try:
            rel = pr.relative_to(base.resolve())
            return f"{USB_MEDIA_PREFIX}{mount_key}:{rel.as_posix()}"
        except ValueError:
            continue
    logger.warning("No stable download id for path outside DATA_ROOT/USB: %s", pr)
    return pr.name


def media_abs_path_from_download_id(file_path: str, data_root: Path) -> Path | None:
    """Resolve a download id to an absolute path, or None if not allowed."""
    if file_path.startswith(USB_MEDIA_PREFIX):
        rest = file_path[len(USB_MEDIA_PREFIX) :]
        idx = rest.find(":")
        if idx < 0:
            return None
        key, rel_s = rest[:idx], rest[idx + 1 :]
        mounts = dict(iter_media_scan_roots())
        base = mounts.get(key)
        if base is None:
            return None
        cand = (base / rel_s).resolve()
        if not cand.is_file():
            return None
        if not cand.is_relative_to(base.resolve()):
            return None
        return cand
    cand = (data_root / file_path).resolve()
    if not cand.is_file():
        return None
    if not cand.is_relative_to(data_root.resolve()):
        return None
    return cand


@dataclass(frozen=True)
class _DiveWindow:
    stem: str
    start: datetime
    end: datetime
    display_name: str


def _parse_iso_to_utc(value: object) -> datetime | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sane_now() -> datetime:
    """Return UTC now, clamped to a reasonable range if the system clock is bogus."""
    now = datetime.now(timezone.utc)
    if now.year < 2024 or now.year > 2030:
        return datetime(2026, 1, 1, tzinfo=timezone.utc)
    return now


def _sane_bounds() -> tuple[datetime, datetime]:
    """Reasonable time window that does not rely on a correct system clock."""
    lo = datetime(2024, 1, 1, tzinfo=timezone.utc)
    hi = datetime(2030, 12, 31, tzinfo=timezone.utc)
    return lo, hi


def _best_start_time(data: dict, file: Path) -> datetime | None:
    """Pick the best available start time for a dive record.

    Priority: started_at (if sane) > release_weight_date+time > file mtime >
    started_at (even if bogus, so the dive still appears in the list).
    """
    lo, hi = _sane_bounds()

    started = _parse_iso_to_utc(data.get("started_at"))
    if started is not None and lo <= started <= hi:
        return started

    rw_date = str(data.get("release_weight_date") or "").strip()
    rw_time = str(data.get("release_weight_time") or "00:00").strip()
    if rw_date:
        try:
            combined = datetime.fromisoformat(f"{rw_date}T{rw_time}:00+00:00")
            if lo <= combined <= hi:
                return combined
        except ValueError:
            pass

    try:
        mtime = datetime.fromtimestamp(file.stat().st_mtime, tz=timezone.utc)
        if lo <= mtime <= hi:
            return mtime
    except OSError:
        pass

    if started is not None:
        return started

    return None


def _dive_display_name(data: dict, dive_file: Path) -> str:
    """Human label for a dive record (shared by window + BIN-log linking)."""
    name = str(data.get("dive_name") or "").strip()
    if name:
        return name
    cfg = str(data.get("configuration") or "").strip()
    return cfg or dive_file.stem.replace("_", " ").title()


def _load_dive_windows(root: Path) -> list[_DiveWindow]:
    """Build time windows from dives/dive_*.json for matching recorder files."""
    ddir = root / "dives"
    if not ddir.is_dir():
        return []

    now = _sane_now()
    lo, hi = _sane_bounds()
    windows: list[_DiveWindow] = []

    for f in sorted(ddir.glob("dive_*.json")):
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        start = _best_start_time(data, f)
        if start is None:
            continue

        ended = _parse_iso_to_utc(data.get("ended_at"))
        if ended is not None and not (lo <= ended <= hi):
            ended = None
        status = str(data.get("status") or "").lower()

        if ended is None:
            if status in ("completed", "cancelled"):
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                    ended = mtime if lo <= mtime <= hi else start + timedelta(hours=6)
                except OSError:
                    ended = start + timedelta(hours=6)
            else:
                ended = now + timedelta(days=3650)

        name = _dive_display_name(data, f)

        windows.append(
            _DiveWindow(stem=f.stem, start=start, end=ended, display_name=name)
        )

    return windows


def _match_dive_window(windows: list[_DiveWindow], t: datetime) -> _DiveWindow | None:
    if not windows:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    else:
        t = t.astimezone(timezone.utc)

    candidates = [w for w in windows if w.start <= t <= w.end]
    if not candidates:
        return None
    candidates.sort(key=lambda w: w.start, reverse=True)
    return candidates[0]


# Archived ArduPilot BIN logs are named ``<dive-slug>_<orig_num>.BIN`` by
# services.binlog._dest_filename (e.g. ``8_17_pool_2_00000113.BIN``).
_ARCHIVED_BIN_RE = re.compile(r"^(?P<slug>.+)_(?P<num>\d+)\.bin$", re.IGNORECASE)


@dataclass(frozen=True)
class _BinLogIndex:
    """Deterministic filename -> dive-name link for archived BIN logs.

    ArduPilot BIN logs frequently carry a bogus mtime (the flight-controller
    RTC is often unset), so timestamp-based dive matching fails and the files
    show up as unlinked "System" data.  The archive step (services.binlog)
    records the exact files it copied in each dive's ``bin_log_files`` and
    names them ``<dive-slug>_<num>.BIN``, so the dive can be recovered from
    the filename alone -- no clock required.
    """

    by_name: dict[str, str]
    by_slug: dict[str, str]

    def lookup(self, filename: str) -> str | None:
        """Return the dive display name for a BIN filename, or None."""
        hit = self.by_name.get(filename)
        if hit:
            return hit
        m = _ARCHIVED_BIN_RE.match(filename)
        if m:
            return self.by_slug.get(m.group("slug").lower())
        return None


def _load_bin_log_index(root: Path) -> _BinLogIndex:
    """Build a :class:`_BinLogIndex` from dives/dive_*.json records."""
    ddir = root / "dives"
    by_name: dict[str, str] = {}
    by_slug: dict[str, str] = {}
    if not ddir.is_dir():
        return _BinLogIndex(by_name, by_slug)

    for f in sorted(ddir.glob("dive_*.json")):
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        display = _dive_display_name(data, f)
        # Primary: exact filenames the archiver actually wrote.
        for entry in data.get("bin_log_files") or []:
            base = Path(str(entry)).name
            if base:
                by_name[base] = display
        # Fallback: reconstruct the slug the archiver would have used, so
        # logs still link even if ``bin_log_files`` is missing/stale.
        slug = slug_for_dive(data, f).lower()
        if slug:
            by_slug.setdefault(slug, display)

    return _BinLogIndex(by_name, by_slug)


def _bin_log_dive_name(
    filename: str, content_kind: MediaType, bin_index: _BinLogIndex | None
) -> str | None:
    """Clock-free dive link for archived ArduPilot BIN logs.

    Preferred over timestamp matching because BIN logs routinely carry a
    bogus mtime; returns None for non-BIN files so they fall back to the
    normal dive-window matching.
    """
    if bin_index is None or content_kind != MediaType.DATA:
        return None
    if not filename.lower().endswith(".bin"):
        return None
    return bin_index.lookup(filename)


def _aggregate_media_paths(
    paths: Iterable[Path], windows: list[_DiveWindow]
) -> dict[str, tuple[int, int]]:
    """Count image/video files whose timestamps fall in dive windows."""
    result: dict[str, tuple[int, int]] = {w.stem: (0, 0) for w in windows}
    if not windows:
        return result
    for path in paths:
        if not path.is_file():
            continue
        ext = path.suffix.lstrip(".").lower()
        if ext not in ALL_EXTENSIONS:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        eff = _effective_created_at(path, st.st_mtime)
        eff_u = eff if eff.tzinfo else eff.replace(tzinfo=timezone.utc)
        wn = _match_dive_window(windows, eff_u)
        if wn is None:
            continue
        mt = _detect_media_type(path.name)
        img, vid = result[wn.stem]
        if mt == MediaType.IMAGE:
            result[wn.stem] = (img + 1, vid)
        elif mt == MediaType.VIDEO:
            result[wn.stem] = (img, vid + 1)
    return result


def aggregate_recorder_media_counts_by_dive_stem(
    root: Path, windows: list[_DiveWindow]
) -> dict[str, tuple[int, int]]:
    """Count image/video files under recorder/ whose timestamp falls in a dive window."""
    result: dict[str, tuple[int, int]] = {w.stem: (0, 0) for w in windows}
    if not windows:
        return result
    rec = root / RECORDER_DIR
    if not rec.is_dir():
        return result
    return _aggregate_media_paths(rec.rglob("*"), windows)


def aggregate_usb_media_counts_by_dive_stem(
    windows: list[_DiveWindow],
) -> dict[str, tuple[int, int]]:
    """Count image/video on mounted USB roots (same windows as recorder)."""
    merged: dict[str, tuple[int, int]] = {w.stem: (0, 0) for w in windows}
    if not windows:
        return merged
    for mount_key, base in iter_media_scan_roots():
        part = _aggregate_media_paths(iter_media_files_on_usb(mount_key, base), windows)
        for stem, (i, v) in part.items():
            oi, ov = merged[stem]
            merged[stem] = (oi + i, ov + v)
    return merged


def _format_duration_seconds(sec: float) -> str | None:
    """Format a duration in seconds as ``Hh Mm`` / ``Mm`` (None if invalid)."""
    if sec is None or not math.isfinite(sec) or sec < 0:
        return None
    total = int(sec)
    h, rem = total // 3600, total % 3600
    m = rem // 60
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _format_dive_duration(start: datetime, end: datetime) -> str:
    out = _format_duration_seconds((end - start).total_seconds())
    return out if out is not None else "—"


def _optional_mission_duration_s(data: dict) -> float | None:
    """On-mission duration (seconds since depth-gate crossing) if persisted."""
    v = data.get("mission_duration_s")
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or x < 0:
        return None
    return x


def _format_lat_lon_display(lat: float, lon: float) -> str:
    def half(v: float, pos: str, neg: str) -> str:
        hem = pos if v >= 0 else neg
        return f"{abs(v):.4f}° {hem}"

    return f"{half(lat, 'N', 'S')}, {half(lon, 'E', 'W')}"


def _location_display(
    data: dict, lat_key: str, lon_key: str, text_key: str | None
) -> str | None:
    """Build a display string from a freeform location or a lat/lon pair."""
    if text_key is not None:
        raw = data.get(text_key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    if lat_key in data and lon_key in data:
        try:
            return _format_lat_lon_display(
                float(data[lat_key]), float(data[lon_key])
            )
        except (TypeError, ValueError):
            return None
    return None


def _optional_depth_m(value: object) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.lower().replace("m", "", 1).strip()
    try:
        return float(s)
    except ValueError:
        return None


def _optional_log_max_depth_m(data: dict) -> float | None:
    """Max depth from logs if persisted on the dive record (not read from MCAP here)."""
    v = data.get("log_max_depth_m")
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x) or x <= 0:
        return None
    return x


def build_dive_history_list(root: Path) -> list[DiveHistoryEntry]:
    """Load dives/dive_*.json newest first with recorder media counts."""
    from .mcap_telemetry import map_dive_stem_to_largest_mcap

    ddir = root / "dives"
    if not ddir.is_dir():
        return []

    windows = _load_dive_windows(root)
    counts = aggregate_recorder_media_counts_by_dive_stem(root, windows)
    usb_counts = aggregate_usb_media_counts_by_dive_stem(windows)
    for stem in counts:
        i, v = counts[stem]
        i2, v2 = usb_counts[stem]
        counts[stem] = (i + i2, v + v2)
    mcap_by_stem = map_dive_stem_to_largest_mcap(root, windows)
    now = _sane_now()
    entries: list[DiveHistoryEntry] = []

    lo, hi = _sane_bounds()

    for f in sorted(ddir.glob("dive_*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        started = _best_start_time(data, f)
        if started is None:
            continue

        stem = f.stem
        ended = _parse_iso_to_utc(data.get("ended_at"))
        if ended is not None and not (lo <= ended <= hi):
            ended = None
        status = str(data.get("status") or "unknown").lower()

        # Prefer the on-mission duration (time since the depth-gate crossing)
        # recorded from the log; it excludes pre-dive surface setup time that
        # inflates the started_at -> ended_at wall clock.
        mission_dur_s = _optional_mission_duration_s(data)
        mission_dur_label = (
            _format_duration_seconds(mission_dur_s) if mission_dur_s is not None else None
        )
        if mission_dur_label is not None:
            duration = mission_dur_label
        elif ended:
            duration = _format_dive_duration(started, ended)
        elif status == "active":
            duration = _format_dive_duration(started, now) + " (ongoing)"
        else:
            duration = "\u2014"

        name = str(data.get("dive_name") or "").strip()
        if not name:
            cfg = str(data.get("configuration") or "").strip()
            name = cfg or stem.replace("_", " ").title()

        img, vid = counts.get(stem, (0, 0))

        start_loc = _location_display(data, "latitude", "longitude", "location")
        end_loc = _location_display(data, "end_latitude", "end_longitude", None)
        # ``location`` stays populated for back-compat (it is the start fix).
        loc = start_loc

        est_depth = _optional_depth_m(data.get("estimated_depth"))
        log_max = _optional_log_max_depth_m(data)
        display_depth = log_max if log_max is not None else est_depth
        mcap_path = mcap_by_stem.get(stem)
        rel_mcap: str | None = None
        if mcap_path is not None:
            rel_mcap = media_download_id_from_abs_path(mcap_path, root)

        bin_log_status: str | None = data.get("bin_log_status") or None
        raw_bin_files = data.get("bin_log_files") or []
        bin_log_rel: list[str] = []
        if isinstance(raw_bin_files, list):
            for raw in raw_bin_files:
                try:
                    p = Path(str(raw))
                except (TypeError, ValueError):
                    continue
                rel_id = media_download_id_from_abs_path(p, root)
                if rel_id:
                    bin_log_rel.append(rel_id)

        entries.append(
            DiveHistoryEntry(
                id=stem,
                name=name,
                status=status,
                date=started,
                duration=duration,
                location=loc,
                start_location=start_loc,
                end_location=end_loc,
                max_depth=display_depth,
                estimated_depth_m=est_depth,
                log_max_depth_m=log_max,
                mcap_relative_path=rel_mcap,
                image_count=img,
                video_count=vid,
                configuration=str(data.get("configuration") or ""),
                bin_log_files=bin_log_rel,
                bin_log_status=bin_log_status,
                processing_state=_opt_str(data.get("processing_state")),
                processing_finished_at=_parse_dt(data.get("processing_finished_at")),
                processing_error=_opt_str(data.get("processing_error")),
            )
        )
    return entries


def _opt_str(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def delete_dive_record_file(root: Path, dive_id: str) -> bool:
    if not re.fullmatch(r"dive_\d{4}", dive_id):
        return False
    path = root / "dives" / f"{dive_id}.json"
    if not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


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
    # Loose YYYYMMDD<sep>HHMMSS anywhere in stem.  ``t``/``T`` covers the
    # recorder's MP4 names (e.g. 20260528t171502_on_bottom.mp4); ``_``/``-``
    # cover dive-stamp prefixes and other DORIS exports.
    m2 = re.search(
        r"(20\d{2})(\d{2})(\d{2})[_\-tT](\d{2})(\d{2})(\d{2})", filename
    )
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
    lo, hi = _sane_bounds()
    mtime_dt = datetime.fromtimestamp(mtime_ts, tz=timezone.utc)

    if lo <= mtime_dt <= hi:
        return mtime_dt

    parsed = _parse_datetime_from_filename(path.name)
    if parsed is not None and lo <= parsed <= hi + timedelta(days=7):
        return parsed

    if mtime_dt > hi:
        return hi
    if mtime_dt < lo:
        return lo
    return mtime_dt


def _usb_file_to_media(
    full_path: Path,
    data_root: Path,
    dive_windows: list[_DiveWindow],
    mount_key: str,
    rel_under_mount: Path,
    bin_index: _BinLogIndex | None = None,
) -> MediaFile:
    """Build a :class:`MediaFile` for a path on an external USB root."""
    stat = full_path.stat()
    eff = _effective_created_at(full_path, stat.st_mtime)
    eff_utc = eff if eff.tzinfo else eff.replace(tzinfo=timezone.utc)
    content_kind = _detect_media_type(full_path.name)
    dive_name = _bin_log_dive_name(full_path.name, content_kind, bin_index)
    media_type = content_kind
    if dive_name is None:
        wn = _match_dive_window(dive_windows, eff_utc)
        if wn:
            dive_name = wn.display_name
    if dive_name is None and content_kind == MediaType.DATA:
        media_type = MediaType.SYSTEM
    fid = f"{USB_MEDIA_PREFIX}{mount_key}:{rel_under_mount.as_posix()}"
    return MediaFile(
        id=fid,
        filename=full_path.name,
        media_type=media_type,
        size_bytes=stat.st_size,
        created_at=eff,
        mission_id=f"usb:{mount_key}",
        dive_name=dive_name,
        download_url=f"/api/v1/media/download?path={quote(fid, safe='')}",
    )


def _file_to_media(
    path: Path,
    root: Path,
    dive_windows: list[_DiveWindow],
    bin_index: _BinLogIndex | None = None,
) -> MediaFile:
    """Convert a filesystem path to a MediaFile model."""
    stat = path.stat()
    rel = path.relative_to(root)
    parts = rel.parts
    mission_id = parts[0] if len(parts) > 1 else None
    eff = _effective_created_at(path, stat.st_mtime)
    eff_utc = eff if eff.tzinfo else eff.replace(tzinfo=timezone.utc)
    top = parts[0].lower() if parts else ""

    # State files stored at DATA_ROOT root (not under a dive folder)
    if len(parts) == 1 and top in (
        "mission_state.json",
        "doris_profile_seq.txt",
    ):
        return MediaFile(
            id=str(rel),
            filename=path.name,
            media_type=MediaType.SYSTEM,
            size_bytes=stat.st_size,
            created_at=eff,
            mission_id=None,
            dive_name=None,
            download_url=f"/api/v1/media/download/{rel}",
        )

    if top in SYSTEM_TOP_LEVEL:
        return MediaFile(
            id=str(rel),
            filename=path.name,
            media_type=MediaType.SYSTEM,
            size_bytes=stat.st_size,
            created_at=eff,
            mission_id=mission_id,
            dive_name=None,
            download_url=f"/api/v1/media/download/{rel}",
        )

    content_kind = _detect_media_type(path.name)
    dive_name: str | None = None
    media_type = content_kind

    # Internal IP-camera recordings live under
    # ``userdata/ipcam_recordings/dive_<stamp>/`` — group them by that
    # per-dive folder rather than the generic "userdata" top-level dir.
    n = len(IPCAM_SUBDIR_PARTS)
    if IPCAM_SUBDIR_PARTS and len(parts) > n and parts[:n] == IPCAM_SUBDIR_PARTS:
        dive_folder = parts[n]
        wn = _match_dive_window(dive_windows, eff_utc)
        dive_name = wn.display_name if wn else dive_folder
        return MediaFile(
            id=str(rel),
            filename=path.name,
            media_type=media_type,
            size_bytes=stat.st_size,
            created_at=eff,
            mission_id=dive_folder,
            dive_name=dive_name,
            download_url=f"/api/v1/media/download/{rel}",
        )

    bin_dive = _bin_log_dive_name(path.name, content_kind, bin_index)
    if top == RECORDER_DIR:
        wn = _match_dive_window(dive_windows, eff_utc)
        dive_name = bin_dive or (wn.display_name if wn else None)
        if dive_name is None and content_kind == MediaType.DATA:
            media_type = MediaType.SYSTEM
    else:
        wn = _match_dive_window(dive_windows, eff_utc)
        if bin_dive:
            dive_name = bin_dive
        elif wn:
            dive_name = wn.display_name
        elif len(parts) > 1:
            dive_name = parts[0]

    return MediaFile(
        id=str(rel),
        filename=path.name,
        media_type=media_type,
        size_bytes=stat.st_size,
        created_at=eff,
        mission_id=mission_id,
        dive_name=dive_name,
        download_url=f"/api/v1/media/download/{rel}",
    )


def _summarize_media_dir(
    entry: Path,
) -> tuple[int, int, int, int, datetime | None]:
    """Count images/videos/data files, total bytes, and the latest mtime."""
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
    return images, videos, data_files, total_size, latest_date


class StorageService:
    """Service for managing stored media files on the local filesystem.

    Uses separate roots for media (recorder data) and configuration storage.
    ``media_root`` defaults to ``RECORDER_ROOT`` (the BlueOS recorder directory
    bind-mounted from the host).  ``root`` is the broader ``DATA_ROOT`` used
    for configurations and other extension data.
    """

    def __init__(
        self,
        root: Path | None = None,
        media_root: Path | None = None,
        ipcam_root: Path | None = None,
    ):
        self.root = root or DATA_ROOT
        self.media_root = media_root or RECORDER_ROOT
        # Internal IP-camera recordings tree (used when no USB is mounted).
        self.ipcam_root = ipcam_root or IPCAM_ROOT
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)
        if not self.media_root.exists() and not self.media_root.is_symlink():
            self.media_root.mkdir(parents=True, exist_ok=True)
        self._dive_windows_cache: list[_DiveWindow] | None = None
        self._bin_index_cache: _BinLogIndex | None = None
        self._dives_dir_mtime: float | None = None

    def _refresh_dive_caches(self) -> None:
        ddir = self.root / "dives"
        mtime = ddir.stat().st_mtime if ddir.is_dir() else 0.0
        if (
            self._dive_windows_cache is not None
            and self._bin_index_cache is not None
            and self._dives_dir_mtime == mtime
        ):
            return
        self._dive_windows_cache = _load_dive_windows(self.root)
        self._bin_index_cache = _load_bin_log_index(self.root)
        self._dives_dir_mtime = mtime

    def _get_dive_windows(self) -> list[_DiveWindow]:
        self._refresh_dive_caches()
        assert self._dive_windows_cache is not None
        return self._dive_windows_cache

    def _get_bin_log_index(self) -> _BinLogIndex:
        self._refresh_dive_caches()
        assert self._bin_index_cache is not None
        return self._bin_index_cache

    async def get_media_files(
        self,
        mission_id: str | None = None,
        media_type: MediaType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MediaFile]:
        """List media files from the recorder tree and from mounted USB volumes."""
        try:
            dive_windows = self._get_dive_windows()
            bin_index = self._get_bin_log_index()
            files: list[MediaFile] = []

            def _append_if_matches(mf: MediaFile) -> None:
                if media_type and mf.media_type != media_type:
                    return
                files.append(mf)

            if mission_id and mission_id.startswith("usb:"):
                mount_key = mission_id[4:]
                mounts = dict(iter_media_scan_roots())
                usb_base = mounts.get(mount_key)
                if usb_base is None or not usb_base.is_dir():
                    return []
                base_r = usb_base.resolve()
                for path in iter_media_files_on_usb(mount_key, usb_base):
                    try:
                        if not path.is_file():
                            continue
                        ext = path.suffix.lstrip(".").lower()
                        if ext not in ALL_EXTENSIONS:
                            continue
                        rel = path.resolve().relative_to(base_r)
                        mf = _usb_file_to_media(
                            path, self.root, dive_windows, mount_key, rel, bin_index
                        )
                        _append_if_matches(mf)
                    except (FileNotFoundError, PermissionError, ValueError):
                        continue
                files.sort(key=lambda f: f.created_at, reverse=True)
                return files[offset : offset + limit]

            if mission_id:
                search_root = self.media_root / mission_id
                if not search_root.exists():
                    # Fall back to the internal IP-camera tree so a dive
                    # recorded without a USB stick is still browsable.
                    alt = self.ipcam_root / mission_id
                    if alt.exists():
                        search_root = alt
                    else:
                        return []
                for path in search_root.rglob("*"):
                    try:
                        if not path.is_file():
                            continue
                        ext = path.suffix.lstrip(".").lower()
                        if ext not in ALL_EXTENSIONS:
                            continue
                        mf = _file_to_media(
                            path, self.root, dive_windows, bin_index
                        )
                        _append_if_matches(mf)
                    except (FileNotFoundError, PermissionError):
                        continue
            else:
                # ``media_root`` and ``ipcam_root`` are sibling trees under
                # DATA_ROOT; the latter only holds files when recording
                # without a USB stick, so scanning both never double-counts.
                for scan_root in (self.media_root, self.ipcam_root):
                    if not scan_root.exists():
                        continue
                    for path in scan_root.rglob("*"):
                        try:
                            if not path.is_file():
                                continue
                            ext = path.suffix.lstrip(".").lower()
                            if ext not in ALL_EXTENSIONS:
                                continue
                            mf = _file_to_media(
                                path, self.root, dive_windows, bin_index
                            )
                            _append_if_matches(mf)
                        except (FileNotFoundError, PermissionError):
                            continue
                for mount_key, usb_base in iter_media_scan_roots():
                    if not usb_base.is_dir():
                        continue
                    try:
                        base_r = usb_base.resolve()
                    except OSError:
                        continue
                    for path in iter_media_files_on_usb(mount_key, usb_base):
                        try:
                            if not path.is_file():
                                continue
                            ext = path.suffix.lstrip(".").lower()
                            if ext not in ALL_EXTENSIONS:
                                continue
                            rel = path.resolve().relative_to(base_r)
                            mf = _usb_file_to_media(
                                path,
                                self.root,
                                dive_windows,
                                mount_key,
                                rel,
                                bin_index,
                            )
                            _append_if_matches(mf)
                        except (FileNotFoundError, PermissionError, ValueError):
                            continue

            files.sort(key=lambda f: f.created_at, reverse=True)

            return files[offset : offset + limit]

        except Exception as e:
            logger.warning(f"Failed to scan media files: {e}")
            raise

    async def get_missions_with_media(self) -> list[MediaMission]:
        """Discover missions from recorder folders and from USB volumes."""
        try:
            missions: list[MediaMission] = []

            if self.media_root.exists():
                for entry in sorted(self.media_root.iterdir(), reverse=True):
                    if not entry.is_dir():
                        continue
                    if entry.name.lower() in SYSTEM_TOP_LEVEL:
                        continue

                    images, videos, data_files, total_size, latest_date = (
                        _summarize_media_dir(entry)
                    )

                    if images + videos + data_files == 0:
                        continue

                    display_name = (
                        "Recorder"
                        if entry.name.lower() == RECORDER_DIR
                        else entry.name
                    )
                    missions.append(
                        MediaMission(
                            mission_id=entry.name,
                            mission_name=display_name,
                            date=latest_date or datetime.now(tz=timezone.utc),
                            image_count=images,
                            video_count=videos,
                            data_file_count=data_files,
                            total_size_bytes=total_size,
                        )
                    )

            # Internal IP-camera dives (recorded without a USB stick).
            if self.ipcam_root.exists() and self.ipcam_root != self.media_root:
                for entry in sorted(self.ipcam_root.iterdir(), reverse=True):
                    if not entry.is_dir():
                        continue
                    images, videos, data_files, total_size, latest_date = (
                        _summarize_media_dir(entry)
                    )
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

            usb_labels = {
                "portable": "USB (portable)",
                "host_mnt": "USB (host /mnt)",
            }
            for mount_key, base in iter_media_scan_roots():
                if not base.is_dir():
                    continue
                images = videos = data_files = 0
                total_size = 0
                latest_date: datetime | None = None
                for path in iter_media_files_on_usb(mount_key, base):
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
                        mission_id=f"usb:{mount_key}",
                        mission_name=usb_labels.get(mount_key, f"USB ({mount_key})"),
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

    async def list_dive_history(self) -> list[DiveHistoryEntry]:
        """Persisted dive records under dives/ (for Previous Dives UI)."""
        return build_dive_history_list(self.root)

    async def delete_dive_record(self, dive_id: str) -> bool:
        ok = delete_dive_record_file(self.root, dive_id)
        if ok:
            self._dive_windows_cache = None
            self._bin_index_cache = None
            self._dives_dir_mtime = None
        return ok

    async def get_file(self, file_path: str) -> bytes | None:
        """Read a media file from ``DATA_ROOT`` or from an indexed USB volume."""
        try:
            full = media_abs_path_from_download_id(file_path, self.root)
            if full is None:
                return None
            return full.read_bytes()
        except Exception as e:
            logger.warning(f"Failed to read file '{file_path}': {e}")
            return None

    async def delete_file(self, file_path: str) -> bool:
        """Delete a media file under ``DATA_ROOT`` or on an indexed USB volume."""
        try:
            full = media_abs_path_from_download_id(file_path, self.root)
            if full is None:
                return False
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
        config.updated_at = datetime.now(timezone.utc)
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
                        created_at=data.get(
                            "created_at", datetime.now(timezone.utc).isoformat()
                        ),
                        updated_at=data.get(
                            "updated_at", datetime.now(timezone.utc).isoformat()
                        ),
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
