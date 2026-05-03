"""On-device post-dive video finalization.

Scans the recorder's output directory for this dive's MPEG-TS
segments and JPEG snapshots (grouped by phase), concatenates the
segments per phase via ``ffmpeg -f concat -c copy``, deletes the
originals once the .mp4 is on disk, and writes a manifest JSON
alongside the outputs so the UI/operator can see what was kept
and what was merged.

Output layout (per dive):

    <recordings>/dive_<stamp>_descent.mp4
    <recordings>/dive_<stamp>_on_bottom.mp4
    <recordings>/dive_<stamp>_ascent.mp4
    <recordings>/dive_<stamp>_manifest.json
    <recordings>/radcam_<stamp>_on_bottom_00001.jpg   (TIMELAPSE mode)
    ...

Invoked at ``POST /api/v1/dive/finalize`` after the Lua dive state
machine reaches RECOVERY.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from . import ip_camera_recorder as iprec
from . import usb_storage

logger = logging.getLogger(__name__)

# Files produced by the recorder pipeline:
#   radcam_20260502_013722_on_bottom_part00_00003.ts
_TS_RE = re.compile(
    r"^radcam_(?P<stamp>\d{8}_\d{6})_(?P<phase>[a-z0-9_]+)"
    r"_part(?P<part>\d{2})_(?P<frag>\d{5})\.ts$"
)

# TIMELAPSE snapshots:
#   radcam_20260502_013722_on_bottom_00001.jpg
_JPG_RE = re.compile(
    r"^radcam_(?P<stamp>\d{8}_\d{6})_(?P<phase>[a-z0-9_]+)"
    r"_(?P<seq>\d{5})\.jpg$"
)


def _recordings_dir() -> Path:
    """Resolve the active recordings directory (USB preferred, then internal)."""
    sub = settings.ipcam_recordings_subdir.strip("/").strip()
    usb = usb_storage.get_recording_dir_if_available(sub)
    if usb is not None:
        return Path(usb)
    data_root = Path(os.environ.get("DORIS_DATA_ROOT", "/tmp/storage"))
    return data_root / sub


async def _ffprobe_duration_s(path: Path) -> float | None:
    """Best-effort duration lookup via ffprobe.  Returns None on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        text = stdout.decode(errors="replace").strip()
        return float(text) if text else None
    except Exception:
        logger.exception("ffprobe failed for %s", path)
        return None


async def _concat_phase(
    phase: str, files: list[Path], out_path: Path,
) -> tuple[bool, str]:
    """Run ``ffmpeg -f concat -c copy`` for one phase.

    Returns ``(success, message)``.  Uses the concat demuxer with a
    sidecar list file so the inputs can be long, use absolute paths,
    and contain special characters (none of our filenames do, but the
    demuxer requires ``-safe 0`` to accept absolute paths regardless).
    Stream copy = zero re-encoding overhead, zero quality loss.
    """
    if not files:
        return False, "no input files"
    list_path = out_path.with_suffix(out_path.suffix + ".concat.txt")
    list_path.write_text(
        "\n".join(f"file '{p}'" for p in files) + "\n",
        encoding="utf-8",
    )

    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        "-movflags", "+faststart",
        "-y", str(out_path),
    ]
    logger.info("FINALIZE ffmpeg: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    try:
        list_path.unlink()
    except FileNotFoundError:
        pass
    if proc.returncode != 0:
        msg = stdout.decode(errors="replace").strip()
        logger.warning(
            "ffmpeg concat failed for phase=%s rc=%s: %s",
            phase, proc.returncode, msg,
        )
        return False, msg or f"ffmpeg rc={proc.returncode}"
    return True, "ok"


async def finalize_dive(stamp: str | None = None) -> dict:
    """Group this dive's segments by phase, concat each phase into MP4.

    If ``stamp`` is omitted, falls back to ``iprec.last_base_stamp()``
    (which the recorder sets on every ``start_recording()`` call).

    Returns a manifest dict.  Does nothing (``success=true``, empty
    phase list) if no matching files exist -- e.g. pure-TIMELAPSE dives
    that never started a video recording are a valid no-op here because
    their JPEGs don't need concatenation.
    """
    started_at = datetime.now(tz=timezone.utc).isoformat()

    # Make sure no pipeline is still writing to the .ts set we're about
    # to read; avoids concatenating a half-written segment.
    if iprec.is_recording():
        logger.info("FINALIZE waiting for active recorder to stop")
        try:
            await iprec.stop_recording()
        except Exception:
            logger.exception("FINALIZE stop_recording raised; continuing")
    # Brief settle for filesystem flushes.
    await asyncio.sleep(0.5)

    dive_stamp = stamp or iprec.last_base_stamp()
    if not dive_stamp:
        return {
            "success": True, "reason": "no_stamp",
            "message": "No active or last-known dive stamp; nothing to finalize",
            "finalize_started_at": started_at,
            "phases": [], "snapshots": [],
        }

    rec_dir = _recordings_dir()
    if not rec_dir.is_dir():
        return {
            "success": True, "reason": "no_recordings_dir",
            "message": f"{rec_dir} does not exist",
            "dive_stamp": dive_stamp,
            "finalize_started_at": started_at,
            "phases": [], "snapshots": [],
        }

    # Bucket files by phase.  Snapshots and segments both embed the
    # dive stamp so we simply filter on it.
    ts_by_phase: dict[str, list[Path]] = {}
    jpg_by_phase: dict[str, list[Path]] = {}
    for p in sorted(rec_dir.iterdir()):
        name = p.name
        m = _TS_RE.match(name)
        if m and m.group("stamp") == dive_stamp:
            ts_by_phase.setdefault(m.group("phase"), []).append(p)
            continue
        m = _JPG_RE.match(name)
        if m and m.group("stamp") == dive_stamp:
            jpg_by_phase.setdefault(m.group("phase"), []).append(p)

    phase_results: list[dict] = []
    for phase, files in sorted(ts_by_phase.items()):
        # part<NN>_<NNNNN>.ts sorts chronologically as plain strings.
        files.sort()
        out_path = rec_dir / f"dive_{dive_stamp}_{phase}.mp4"
        source_bytes = sum((f.stat().st_size for f in files if f.exists()), 0)
        logger.info(
            "FINALIZE phase=%s inputs=%d source_bytes=%d -> %s",
            phase, len(files), source_bytes, out_path,
        )
        ok, msg = await _concat_phase(phase, files, out_path)
        entry: dict = {
            "phase": phase,
            "input_count": len(files),
            "input_bytes": source_bytes,
            "inputs_deleted": 0,
            "output": str(out_path),
            "output_bytes": 0,
            "output_duration_s": None,
            "success": ok,
            "message": msg,
        }
        if ok:
            dur = await _ffprobe_duration_s(out_path)
            entry["output_duration_s"] = dur
            entry["output_bytes"] = (
                out_path.stat().st_size if out_path.exists() else 0
            )
            # Delete the originals only on a successful concat.  On
            # failure we keep the .ts segments so the operator can
            # recover manually.
            removed = 0
            for f in files:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
            entry["inputs_deleted"] = removed
        phase_results.append(entry)

    snapshot_results: list[dict] = []
    for phase, files in sorted(jpg_by_phase.items()):
        files.sort()
        total = sum(
            (f.stat().st_size for f in files if f.exists()), 0,
        )
        snapshot_results.append({
            "phase": phase,
            "count": len(files),
            "total_bytes": total,
            "paths": [str(f) for f in files],
        })

    # Reset the snapshot sequence counters so the next dive starts from seq=1.
    iprec.clear_snapshot_state()

    finished_at = datetime.now(tz=timezone.utc).isoformat()
    manifest = {
        "dive_stamp": dive_stamp,
        "recordings_dir": str(rec_dir),
        "finalize_started_at": started_at,
        "finalize_finished_at": finished_at,
        "phases": phase_results,
        "snapshots": snapshot_results,
        "success": all(p["success"] for p in phase_results) if phase_results else True,
    }

    manifest_path = rec_dir / f"dive_{dive_stamp}_manifest.json"
    try:
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    except Exception as e:
        logger.exception("FINALIZE manifest write failed: %s", e)
        manifest["manifest_write_error"] = str(e)
    else:
        manifest["manifest"] = str(manifest_path)

    logger.info(
        "FINALIZE complete: stamp=%s phases=%d snapshots=%d manifest=%s",
        dive_stamp, len(phase_results), len(snapshot_results), manifest_path,
    )
    return manifest
