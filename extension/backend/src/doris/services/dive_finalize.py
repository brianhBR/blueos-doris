"""On-device post-dive video finalization.

Scans the recorder's output directory for this dive's MPEG-TS
segments and JPEG snapshots (grouped by phase), produces per-phase
MP4 outputs, deletes the originals once the .mp4s are on disk, and
writes a manifest JSON alongside the outputs so the UI/operator can
see what was kept and what was merged.

Bottom-phase output depends on the bottom camera mode
(``DORIS_BTM_CMOD`` / ``bottom_mode`` arg):

* CONTINUOUS (1): splitmuxsink rotated the on-bottom recording at
  5-minute boundaries, so each ``.ts`` is already an independent
  chunk.  We lossless-remux each one to its own MP4
  ``dive_<stamp>_on_bottom_chunkNN.mp4`` (``ffmpeg -i .. -c copy``;
  no concat, no re-encode).
* INTERVAL (2): each ipcam start/stop cycle is a separate session
  (distinct ``part<NN>`` in the .ts filename).  We group ``.ts``
  files by part and concat each group into one MP4
  ``dive_<stamp>_on_bottom_videoNN.mp4`` where ``NN`` is the
  chronological session index (1-based, contiguous even if part
  numbers are sparse).
* TIMELAPSE (3): no .ts files exist; only JPEG snapshots, which are
  cataloged but never re-encoded into video.
* OFF / unknown / legacy: falls through to the historical behavior
  of concatenating every bottom .ts into a single
  ``dive_<stamp>_on_bottom.mp4`` so older dives still finalize
  cleanly.

Descent and ascent always produce one MP4 per phase
(``dive_<stamp>_descent.mp4`` / ``_ascent.mp4``) regardless of
bottom mode.

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


async def _remux_one(src: Path, out_path: Path) -> tuple[bool, str]:
    """Lossless container swap from MPEG-TS to MP4.

    Used for CONTINUOUS bottom mode where splitmuxsink already rotated
    the recording at 5-minute boundaries, so each ``.ts`` IS the
    intended chunk -- we only need to wrap it in MP4.  Stream-copy
    only (``-c copy``); no transcoding, bit-exact.
    """
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
        "-i", str(src),
        "-c", "copy",
        "-movflags", "+faststart",
        "-y", str(out_path),
    ]
    logger.info("FINALIZE ffmpeg remux: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        msg = stdout.decode(errors="replace").strip()
        logger.warning(
            "ffmpeg remux failed for %s rc=%s: %s",
            src.name, proc.returncode, msg,
        )
        return False, msg or f"ffmpeg rc={proc.returncode}"
    return True, "ok"


async def _finalize_remux_chunks(
    files: list[Path], rec_dir: Path, dive_stamp: str,
) -> list[dict]:
    """CONTINUOUS bottom: one MP4 per .ts via lossless remux.

    Files are already chronological (caller sorted them).  Output
    chunks are numbered chronologically starting at 01.  On any
    individual chunk failure, the source .ts is preserved so the
    operator can recover manually.
    """
    results: list[dict] = []
    for idx, src in enumerate(files, start=1):
        out_path = rec_dir / (
            f"dive_{dive_stamp}_on_bottom_chunk{idx:02d}.mp4"
        )
        src_bytes = src.stat().st_size if src.exists() else 0
        logger.info(
            "FINALIZE on_bottom chunk %02d: %s (%d B) -> %s",
            idx, src.name, src_bytes, out_path,
        )
        ok, msg = await _remux_one(src, out_path)
        entry: dict = {
            "phase": "on_bottom",
            "kind": "chunk",
            "index": idx,
            "input_count": 1,
            "input_bytes": src_bytes,
            "inputs_deleted": 0,
            "output": str(out_path),
            "output_bytes": 0,
            "output_duration_s": None,
            "success": ok,
            "message": msg,
        }
        if ok:
            entry["output_duration_s"] = await _ffprobe_duration_s(out_path)
            entry["output_bytes"] = (
                out_path.stat().st_size if out_path.exists() else 0
            )
            try:
                src.unlink()
                entry["inputs_deleted"] = 1
            except OSError:
                pass
        results.append(entry)
    return results


async def _finalize_concat_interval_sessions(
    files: list[Path], rec_dir: Path, dive_stamp: str,
) -> list[dict]:
    """VIDEO_INTERVAL bottom: one MP4 per ipcam start/stop cycle.

    A cycle is identified by the ``part<NN>`` token in the source .ts
    filename: each ``ipcam_start`` claims a fresh part offset so all
    .ts files written by a single session share that part number.
    Output sessions are renumbered chronologically (``video01``,
    ``video02``, ...) so the MP4 list is contiguous even if some part
    numbers were never used (e.g. cancelled descent recording leaving
    a gap).
    """
    by_part: dict[str, list[Path]] = {}
    for f in files:
        m = _TS_RE.match(f.name)
        if not m:
            continue
        by_part.setdefault(m.group("part"), []).append(f)

    results: list[dict] = []
    for idx, part_key in enumerate(sorted(by_part.keys()), start=1):
        group = sorted(by_part[part_key])
        out_path = rec_dir / (
            f"dive_{dive_stamp}_on_bottom_video{idx:02d}.mp4"
        )
        src_bytes = sum((f.stat().st_size for f in group if f.exists()), 0)
        logger.info(
            "FINALIZE on_bottom video %02d (part=%s, inputs=%d, %d B) -> %s",
            idx, part_key, len(group), src_bytes, out_path,
        )
        ok, msg = await _concat_phase("on_bottom", group, out_path)
        entry: dict = {
            "phase": "on_bottom",
            "kind": "session",
            "index": idx,
            "part": part_key,
            "input_count": len(group),
            "input_bytes": src_bytes,
            "inputs_deleted": 0,
            "output": str(out_path),
            "output_bytes": 0,
            "output_duration_s": None,
            "success": ok,
            "message": msg,
        }
        if ok:
            entry["output_duration_s"] = await _ffprobe_duration_s(out_path)
            entry["output_bytes"] = (
                out_path.stat().st_size if out_path.exists() else 0
            )
            removed = 0
            for f in group:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
            entry["inputs_deleted"] = removed
        results.append(entry)
    return results


async def finalize_dive(
    stamp: str | None = None, bottom_mode: int | None = None,
) -> dict:
    """Group this dive's segments by phase, produce per-phase MP4s.

    If ``stamp`` is omitted, falls back to ``iprec.last_base_stamp()``
    (which the recorder sets on every ``start_recording()`` call).

    ``bottom_mode`` is the value of ``DORIS_BTM_CMOD`` for this dive
    (1=continuous, 2=interval, 3=timelapse).  Controls the
    on_bottom-phase output strategy (see module docstring).  When
    ``None`` (legacy callers, manual curl, or older Lua) the on_bottom
    phase falls back to the historical concat-into-one-MP4 behavior so
    pre-existing dives still finalize cleanly.

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

    rec_root = _recordings_dir()
    if not rec_root.is_dir():
        return {
            "success": True, "reason": "no_recordings_dir",
            "message": f"{rec_root} does not exist",
            "dive_stamp": dive_stamp,
            "finalize_started_at": started_at,
            "phases": [], "snapshots": [],
        }

    # Per-dive subfolder is where the recorder writes today.  The flat
    # recordings root is also scanned so that any half-finished dive
    # from a previous (pre-subfolder) build still finalises cleanly.
    dive_dir = rec_root / f"dive_{dive_stamp}"
    scan_dirs: list[Path] = []
    if dive_dir.is_dir():
        scan_dirs.append(dive_dir)
    scan_dirs.append(rec_root)

    # Bucket files by phase.  Snapshots and segments both embed the
    # dive stamp so we filter on it; we prefer the per-dive folder
    # but fall back to the flat root for legacy artifacts.
    ts_by_phase: dict[str, list[Path]] = {}
    jpg_by_phase: dict[str, list[Path]] = {}
    seen: set[Path] = set()
    for d in scan_dirs:
        try:
            entries = sorted(d.iterdir())
        except FileNotFoundError:
            continue
        for p in entries:
            if p in seen or not p.is_file():
                continue
            seen.add(p)
            name = p.name
            m = _TS_RE.match(name)
            if m and m.group("stamp") == dive_stamp:
                ts_by_phase.setdefault(m.group("phase"), []).append(p)
                continue
            m = _JPG_RE.match(name)
            if m and m.group("stamp") == dive_stamp:
                jpg_by_phase.setdefault(m.group("phase"), []).append(p)

    # Write finalized MP4s + manifest into the per-dive folder.  Create
    # it now (as a no-op if it already exists) so even a flat-only
    # legacy dive ends up consolidated.
    rec_dir = dive_dir
    rec_dir.mkdir(parents=True, exist_ok=True)

    phase_results: list[dict] = []
    for phase, files in sorted(ts_by_phase.items()):
        # part<NN>_<NNNNN>.ts sorts chronologically as plain strings.
        files.sort()
        # Bottom phase: split by mode.  Continuous -> one MP4 per .ts
        # (lossless remux); Interval -> one MP4 per ipcam start/stop
        # cycle (concat per part group).  Other modes / unknown ->
        # historical concat-all-into-one (regression-safe).
        if phase == "on_bottom" and bottom_mode == 1:
            phase_results.extend(
                await _finalize_remux_chunks(files, rec_dir, dive_stamp)
            )
            continue
        if phase == "on_bottom" and bottom_mode == 2:
            phase_results.extend(
                await _finalize_concat_interval_sessions(
                    files, rec_dir, dive_stamp,
                )
            )
            continue

        out_path = rec_dir / f"dive_{dive_stamp}_{phase}.mp4"
        source_bytes = sum((f.stat().st_size for f in files if f.exists()), 0)
        logger.info(
            "FINALIZE phase=%s inputs=%d source_bytes=%d -> %s",
            phase, len(files), source_bytes, out_path,
        )
        ok, msg = await _concat_phase(phase, files, out_path)
        entry: dict = {
            "phase": phase,
            "kind": "single",
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
        "bottom_mode": bottom_mode,
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
