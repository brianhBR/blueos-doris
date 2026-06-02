"""On-device post-dive video finalization.

Scans the recorder's output directory for this dive's MPEG-TS
segments and JPEG snapshots (grouped by phase), produces per-phase
MP4 outputs, deletes the originals once the .mp4s are on disk, and
writes a manifest JSON alongside the outputs so the UI/operator can
see what was kept and what was merged.

Bottom-phase output depends on the bottom camera mode
(``DORIS_BTM_CMOD`` / ``bottom_mode`` arg):

* CONTINUOUS (1): the recorder rotates ``.ts`` files internally and
  ALSO produces a fresh ``.ts`` whenever an RTSP teardown forces a
  pipeline rebuild, so the raw ``.ts`` count is unpredictable.  We
  ignore the raw layout entirely and run a two-pass lossless ffmpeg
  pipeline: concat every bottom ``.ts`` into a staging
  ``.bottom_full.ts``, then ``-f segment -segment_time 300
  -reset_timestamps 1`` to slice it into clean 5-minute MP4 chunks.
  Chunk count is therefore proportional to dive duration (one chunk
  per ~5 minutes), not to RTSP stability.
* INTERVAL (2): each ipcam start/stop cycle should yield one MP4,
  even if the cycle's recording was split across several ``.ts``
  files by mid-cycle RTSP rebuilds.  Cycles are identified
  deterministically via the ``cyc<CC>`` filename tag the recorder
  stamps onto every ``.ts`` (a counter that increments once per
  ``ipcam_start`` and stays constant through every watchdog rebuild
  inside that cycle).  No timing heuristic is involved -- pause
  length, reconnect duration, restart count: irrelevant.  All ``.ts``
  files sharing one ``<CC>`` are lossless-concatenated to one MP4.
  Pre-upgrade dives whose ``.ts`` files lack the ``<CC>`` tag fall
  back to the historical ``part<NN>`` grouping with a ``legacy_``
  prefix on the output filename so the operator can spot the bridge
  case.
* TIMELAPSE (3): no .ts files exist; only JPEG snapshots, which are
  cataloged but never re-encoded into video.
* OFF / unknown / legacy: falls through to the historical behavior
  of concatenating every bottom .ts into a single MP4 so older dives
  still finalize cleanly.

The descent and ascent phases are recorded as a single continuous
stream; they are re-segmented into 5-minute MP4 chunks via the same
two-pass pipeline as continuous bottom (issue #33) so file sizes stay
consistent and manageable regardless of phase duration.

Output MP4s are named ``<recstart>_<phase>.mp4`` (e.g.
``20260528t171502_on_bottom.mp4``), where ``recstart`` is the UTC
``YYYYMMDDtHHMMSS`` the recording's footage started -- the earliest
``.ts`` fragment open-stamp for that output (or, for continuous
chunks, footage-start plus the cumulative duration of preceding
chunks).  Files live inside the per-dive ``dive_<stamp>/`` folder, so
dive grouping is preserved by directory.  Pre-upgrade ``.ts`` that
lack an open-stamp fall back to the historical index-based names
(``dive_<stamp>_on_bottom_videoNN.mp4`` / ``_chunkNN.mp4`` /
``_<phase>.mp4``).

Invoked at ``POST /api/v1/dive/finalize`` after the Lua dive state
machine reaches RECOVERY.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import settings
from . import ip_camera_recorder as iprec
from . import persistent_log, usb_storage

logger = logging.getLogger(__name__)

# Files produced by the recorder pipeline.  Two filename forms are
# accepted; both must round-trip through this regex so finalize can
# group across phases regardless of which build produced the file.
#
# New (post cyc<CC> upgrade):
#   radcam_20260502_013722_on_bottom_cyc02_part10_00003.ts
# Legacy (pre cyc<CC> upgrade):
#   radcam_20260502_013722_on_bottom_part00_00003.ts
#
# ``phase`` is non-greedy so it doesn't swallow the optional
# ``_cyc<CC>`` segment; the ``_part<NN>_<NNNNN>.ts`` suffix anchors
# the trailing portion either way.  ``cyc`` is ``None`` for legacy
# files and finalize routes those through the per-part fallback.
#
# Newest (post per-fragment open-stamp upgrade) adds a trailing
# ``_t<YYYYMMDDtHHMMSS>`` carrying the fragment's open wall-clock (UTC):
#   radcam_20260502_013722_on_bottom_cyc02_part10_00003_t20260502t013744.ts
# The ``start`` group is optional so pre-upgrade .ts still match (those
# fall back to index-based MP4 naming).
_TS_RE = re.compile(
    r"^radcam_(?P<stamp>\d{8}_\d{6})_(?P<phase>[a-z0-9_]+?)"
    r"(?:_cyc(?P<cyc>\d{2}))?"
    r"_part(?P<part>\d{2})_(?P<frag>\d{5})"
    r"(?:_t(?P<start>\d{8}t\d{6}))?\.ts$"
)

# TIMELAPSE snapshots:
#   radcam_20260502_013722_on_bottom_00001.jpg
_JPG_RE = re.compile(
    r"^radcam_(?P<stamp>\d{8}_\d{6})_(?P<phase>[a-z0-9_]+)"
    r"_(?P<seq>\d{5})\.jpg$"
)


# Output MP4 naming.  Operator-facing video files are named for *when
# the recording actually started* rather than a synthetic index:
#   <recstart>_<phase>.mp4   e.g. 20260528t171502_on_bottom.mp4
# ``recstart`` is the UTC ``YYYYMMDDtHHMMSS`` of the earliest fragment in
# the group (first frame on disk).  They live inside the per-dive
# ``dive_<stamp>/`` folder, so the dive grouping is preserved by
# directory and ``recstart`` is globally unique in time (no collisions
# across cycles, chunks, or dives).  Pre-upgrade .ts that lack the
# open-stamp fall back to the historical index-based names below.
_START_FMT = "%Y%m%dt%H%M%S"


def _group_start_stamp(files: list[Path]) -> str | None:
    """Earliest fragment open-stamp (``YYYYMMDDtHHMMSS``) across a group.

    The recorder tags every ``.ts`` with the wall-clock UTC time its
    fragment opened (``_t<stamp>``); for the first fragment of a
    recording that's the moment real frames started landing on disk.
    The minimum across the group is therefore when that output's footage
    began.  Fixed-width zero-padded stamps mean lexical ``min`` ==
    chronological ``min``.  Returns ``None`` when no file carries the tag
    (legacy .ts predating the stamp) so callers fall back to index-based
    naming.
    """
    stamps = [
        m.group("start")
        for f in files
        if (m := _TS_RE.match(f.name)) and m.group("start")
    ]
    return min(stamps) if stamps else None


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


def _copy_diagnostic_logs(rec_dir: Path) -> dict:
    """Copy the persistent doris/dmesg logs into ``<dive_dir>/logs/``.

    The canonical logs live on *internal* storage
    (``persistent_log.LOG_DIR``), but the recordings (and thus this dive
    folder) frequently live on a USB stick the operator pulls -- so the
    logs are otherwise easy to miss.  Copying ``doris.log*`` and
    ``dmesg.log*`` here makes the whole diagnostic bundle travel with the
    recordings.

    Synchronous (runs in an executor); strictly best-effort -- a copy
    failure is logged and summarised but never raises into finalize.
    Returns a small summary dict for the manifest.
    """
    src_dir = persistent_log.LOG_DIR
    out: dict = {"source": str(src_dir), "copied": [], "errors": []}
    try:
        if not src_dir.is_dir():
            out["skipped"] = "log_dir_missing"
            return out
        logs_dir = rec_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        for src in sorted(src_dir.iterdir()):
            if not src.is_file():
                continue
            if not (src.name.startswith("doris.log")
                    or src.name.startswith("dmesg.log")):
                continue
            try:
                shutil.copy2(src, logs_dir / src.name)
                out["copied"].append(src.name)
            except OSError as e:
                logger.warning("FINALIZE log copy failed for %s: %s", src.name, e)
                out["errors"].append(f"{src.name}: {e}")
        out["dest"] = str(logs_dir)
    except Exception as e:
        logger.exception("FINALIZE log copy step failed: %s", e)
        out["errors"].append(str(e))
    return out


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


# Generous wallclock cap on the big two-pass continuous-mode ffmpeg
# steps.  A 30-min dive at 50 Mbit/s pushes ~12 GB through each pass
# (concat reads all .ts and writes the staging .ts; segment reads the
# staging .ts and writes the chunked MP4s).  On a USB-SSD-class disk
# both passes complete in < 5 min; the cap below is set well above
# that so a slow device still completes rather than aborting mid-dive.
_FFMPEG_BIG_TIMEOUT_S = 1200.0


async def _ffmpeg_concat_ts(
    files: list[Path], out_ts: Path, timeout_s: float,
) -> tuple[bool, str]:
    """Lossless concat of MPEG-TS inputs to a single MPEG-TS output.

    Uses the concat demuxer with ``-c copy -f mpegts`` so the output
    container matches the inputs exactly and no re-encoding occurs.
    Used to build the staging ``.bottom_full.ts`` for CONTINUOUS-mode
    re-segmentation.  Sidecar list file enables long absolute-path
    inputs (``-safe 0``).
    """
    if not files:
        return False, "no input files"
    list_path = out_ts.with_suffix(out_ts.suffix + ".concat.txt")
    list_path.write_text(
        "\n".join(f"file '{p}'" for p in files) + "\n",
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        "-f", "mpegts",
        "-y", str(out_ts),
    ]
    logger.info("FINALIZE ffmpeg concat->ts: %s", " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return False, f"ffmpeg concat->ts timed out after {timeout_s:.0f}s"
    finally:
        try:
            list_path.unlink()
        except FileNotFoundError:
            pass
    if proc.returncode != 0:
        msg = stdout.decode(errors="replace").strip()
        logger.warning(
            "ffmpeg concat->ts failed rc=%s: %s", proc.returncode, msg,
        )
        return False, msg or f"ffmpeg rc={proc.returncode}"
    return True, "ok"


async def _ffmpeg_segment_ts_to_mp4(
    in_ts: Path,
    out_pattern: str,
    segment_time_s: int,
    timeout_s: float,
) -> tuple[bool, str]:
    """Slice an MPEG-TS file into MP4 chunks at fixed time boundaries.

    Lossless stream-copy via the segment muxer.  ``-reset_timestamps
    1`` rewrites each output's PTS to 0 so session-boundary jumps
    inside the staging .ts (left over from RTSP-rebuilt sub-segments)
    don't leak into output player/seekbar behavior.  ffmpeg picks the
    nearest keyframe to each requested boundary, so chunks may land
    at ``segment_time_s`` ±2 s -- this is expected and correct for
    stream-copy.
    """
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
        "-i", str(in_ts),
        "-c", "copy",
        "-f", "segment",
        "-segment_time", str(int(segment_time_s)),
        "-reset_timestamps", "1",
        "-movflags", "+faststart",
        "-y", out_pattern,
    ]
    logger.info("FINALIZE ffmpeg segment: %s", " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return False, f"ffmpeg segment timed out after {timeout_s:.0f}s"
    if proc.returncode != 0:
        msg = stdout.decode(errors="replace").strip()
        logger.warning(
            "ffmpeg segment failed rc=%s: %s", proc.returncode, msg,
        )
        return False, msg or f"ffmpeg rc={proc.returncode}"
    return True, "ok"


async def _finalize_continuous_resegment(
    files: list[Path], rec_dir: Path, dive_stamp: str,
    phase: str = "on_bottom",
) -> list[dict]:
    """Concat all of one phase's ``.ts`` then slice into 5-min MP4s.

    Used for every continuously-recorded phase: CONTINUOUS bottom
    (``DORIS_BTM_CMOD=1``) and the descent/ascent phases (issue #33),
    which are recorded as a single continuous stream and should be
    chunked into 5-minute segments for consistency and data management.

    Two-pass lossless ffmpeg pipeline:

    1. Concat every ``.ts`` for ``phase`` into a staging
       ``<rec_dir>/.<phase>_full.ts`` (``-c copy -f mpegts``).
    2. Re-segment the staging file at 5-minute boundaries with
       ``-f segment -segment_time 300 -reset_timestamps 1`` directly
       to ``dive_<stamp>_<phase>_chunkNN.mp4``.

    The output count is therefore proportional to phase duration, not
    to how many times the camera-side RTSP server tore down the
    pipeline.  ffmpeg's segment muxer numbers outputs from 00; we
    rename ``chunk00`` -> ``chunk01`` etc. so the operator-visible
    numbering is 1-based and matches the rest of the codebase.

    On success, deletes both the staging file AND the source ``.ts``
    files.  On any ffmpeg failure, leaves all inputs in place so the
    operator can recover manually.
    """
    if not files:
        return []

    # Per-phase staging name so descent/on_bottom/ascent re-segmentation
    # in the same dive never collide on one shared staging file.
    staging = rec_dir / f".{phase}_full.ts"
    src_bytes = sum((f.stat().st_size for f in files if f.exists()), 0)
    logger.info(
        "FINALIZE %s continuous: concat %d .ts (%d B) -> %s",
        phase, len(files), src_bytes, staging,
    )

    summary: dict = {
        "phase": phase,
        "kind": "continuous_resegment",
        "input_count": len(files),
        "input_bytes": src_bytes,
        "inputs_deleted": 0,
        "staging": str(staging),
        "outputs": [],
        "success": False,
        "message": "",
    }

    ok, msg = await _ffmpeg_concat_ts(files, staging, _FFMPEG_BIG_TIMEOUT_S)
    if not ok:
        summary["message"] = f"concat: {msg}"
        # Best-effort cleanup of partial staging file.
        try:
            if staging.exists():
                staging.unlink()
        except OSError:
            pass
        return [summary]

    out_pattern = str(
        rec_dir / f"dive_{dive_stamp}_{phase}_chunk%02d.mp4"
    )
    ok, msg = await _ffmpeg_segment_ts_to_mp4(
        staging, out_pattern, 300, _FFMPEG_BIG_TIMEOUT_S,
    )
    if not ok:
        summary["message"] = f"segment: {msg}"
        # Leave staging on disk so the operator can re-segment manually.
        return [summary]

    # Drop the staging file -- the chunks are the canonical artifacts.
    try:
        staging.unlink()
    except OSError:
        pass

    # Match the 0-based chunks ffmpeg just wrote (chunk00, chunk01, ...),
    # filtering out any chunk01+ a previous run may have left on disk.
    zero_based_re = re.compile(
        r"^dive_" + re.escape(dive_stamp)
        + r"_" + re.escape(phase) + r"_chunk(\d{2})\.mp4$"
    )
    produced: list[tuple[int, Path]] = []
    for p in rec_dir.glob(f"dive_{dive_stamp}_{phase}_chunk*.mp4"):
        m = zero_based_re.match(p.name)
        if m:
            produced.append((int(m.group(1)), p))
    produced.sort(key=lambda t: t[0])

    # Re-segmented chunks don't align to .ts fragment boundaries, so
    # derive each chunk's start by walking actual durations forward from
    # when this phase's footage began (the earliest fragment's open-stamp).
    staging_start = _group_start_stamp(files)
    start_dt: datetime | None = None
    if staging_start:
        try:
            start_dt = datetime.strptime(staging_start, _START_FMT).replace(
                tzinfo=timezone.utc,
            )
        except ValueError:
            start_dt = None

    chunk_entries: list[dict] = []
    renamed: list[Path] = []
    if start_dt is not None:
        # Timestamped names: <chunkstart>_<phase>.mp4, where chunkstart
        # = footage start + cumulative duration of preceding chunks.
        cumulative = 0.0
        for idx, (_idx0, p) in enumerate(produced, start=1):
            dur = await _ffprobe_duration_s(p)
            stamp = (start_dt + timedelta(seconds=cumulative)).strftime(
                _START_FMT,
            )
            new_path = rec_dir / f"{stamp}_{phase}.mp4"
            try:
                p.rename(new_path)
            except OSError as e:
                logger.warning(
                    "FINALIZE could not rename %s -> %s: %s", p, new_path, e,
                )
                new_path = p
            renamed.append(new_path)
            out_bytes = new_path.stat().st_size if new_path.exists() else 0
            chunk_entries.append({
                "phase": phase,
                "kind": "chunk",
                "index": idx,
                "rec_start": stamp,
                "output": str(new_path),
                "output_bytes": out_bytes,
                "output_duration_s": dur,
                "success": True,
                "message": "ok",
            })
            # Assume the configured segment length on a probe failure so
            # the next chunk's start still advances (avoids a collision).
            cumulative += dur if dur is not None else 300.0
    else:
        # Legacy fallback (pre open-stamp .ts): 1-based chunkNN names.
        # Rename highest-first to avoid clobbering as chunkNN ->
        # chunk(NN+1).
        for idx0, p in sorted(produced, key=lambda t: t[0], reverse=True):
            new_path = rec_dir / (
                f"dive_{dive_stamp}_{phase}_chunk{idx0 + 1:02d}.mp4"
            )
            try:
                p.rename(new_path)
                renamed.append(new_path)
            except OSError as e:
                logger.warning(
                    "FINALIZE could not rename %s -> %s: %s", p, new_path, e,
                )
                renamed.append(p)
        renamed.sort(key=lambda p: p.name)
        for idx, out_path in enumerate(renamed, start=1):
            out_bytes = out_path.stat().st_size if out_path.exists() else 0
            dur = await _ffprobe_duration_s(out_path)
            chunk_entries.append({
                "phase": phase,
                "kind": "chunk",
                "index": idx,
                "output": str(out_path),
                "output_bytes": out_bytes,
                "output_duration_s": dur,
                "success": True,
                "message": "ok",
            })

    # All chunks landed; safe to drop the source .ts files.
    removed = 0
    for f in files:
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    summary["inputs_deleted"] = removed

    summary["outputs"] = [str(p) for p in renamed]
    summary["success"] = True
    summary["message"] = f"produced {len(renamed)} chunks"
    return [summary, *chunk_entries]


def _split_tagged_legacy(
    files: list[Path],
) -> tuple[dict[str, list[Path]], dict[str, list[Path]]]:
    """Bucket bottom .ts files by their ``<CC>`` cycle tag.

    Returns ``(by_cyc, by_legacy_part)``:

    * ``by_cyc``: ``cyc<CC>`` value -> list of files whose filenames
      carry that tag.  Used by the new deterministic interval-cycle
      path.
    * ``by_legacy_part``: ``part<NN>`` value -> list of files lacking
      a ``cyc`` tag.  Used only by the legacy fallback path that
      bridges pre-upgrade dives still on disk.

    Files that don't match ``_TS_RE`` at all are silently dropped
    (caller has already filtered to bottom-phase .ts).
    """
    by_cyc: dict[str, list[Path]] = {}
    by_legacy_part: dict[str, list[Path]] = {}
    for f in files:
        m = _TS_RE.match(f.name)
        if not m:
            continue
        cyc = m.group("cyc")
        if cyc is not None:
            by_cyc.setdefault(cyc, []).append(f)
        else:
            by_legacy_part.setdefault(m.group("part"), []).append(f)
    for v in by_cyc.values():
        v.sort()
    for v in by_legacy_part.values():
        v.sort()
    return by_cyc, by_legacy_part


async def _finalize_interval_by_cyc(
    files: list[Path], rec_dir: Path, dive_stamp: str,
) -> list[dict]:
    """INTERVAL bottom: one MP4 per ipcam_start/stop cycle, via cyc<CC>.

    The recorder stamps every ``.ts`` it writes for one
    ``ipcam_start / ipcam_stop`` cycle with the same ``cyc<CC>`` tag,
    incremented once per cycle.  Watchdog-driven mid-cycle pipeline
    rebuilds reuse the live cycle tag, so the regex-grouped
    ``cyc<CC>`` -> file list is a deterministic, complete description
    of the cycle's contents.  No mtime / pause-length heuristics are
    consulted.

    Output is named ``dive_<stamp>_on_bottom_videoNN.mp4`` where
    ``NN`` is the chronological 1-based cycle index (so ``video01``
    is the earliest ``cyc<CC>`` that produced bottom .ts even if
    ``<CC>`` itself is, say, ``02`` because the dive's first cycle
    was the descent recording).

    Files lacking a ``cyc<CC>`` tag (legacy build) are routed through
    :func:`_finalize_interval_by_part_legacy` and emitted with a
    ``legacy_`` filename prefix so the operator can tell them apart.
    """
    by_cyc, by_legacy_part = _split_tagged_legacy(files)

    results: list[dict] = []
    # cyc tags are zero-padded 2-digit strings, so lex-sort == numeric
    # ascending order == chronological order (within one dive the
    # counter is monotonic).
    for idx, cyc_key in enumerate(sorted(by_cyc.keys()), start=1):
        group = by_cyc[cyc_key]
        # Name from when this cycle's footage actually started; fall back
        # to the chronological 1-based index for legacy untagged .ts.
        recstart = _group_start_stamp(group)
        if recstart:
            out_path = rec_dir / f"{recstart}_on_bottom.mp4"
        else:
            out_path = rec_dir / f"dive_{dive_stamp}_on_bottom_video{idx:02d}.mp4"
        src_bytes = sum((f.stat().st_size for f in group if f.exists()), 0)
        logger.info(
            "FINALIZE on_bottom video %02d (cyc=%s, inputs=%d, %d B) -> %s",
            idx, cyc_key, len(group), src_bytes, out_path,
        )
        ok, msg = await _concat_phase("on_bottom", group, out_path)
        entry: dict = {
            "phase": "on_bottom",
            "kind": "cycle",
            "index": idx,
            "cyc": cyc_key,
            "rec_start": recstart,
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

    if by_legacy_part:
        results.extend(
            await _finalize_interval_by_part_legacy(
                by_legacy_part, rec_dir, dive_stamp,
            )
        )
    return results


async def _finalize_interval_by_part_legacy(
    by_part: dict[str, list[Path]], rec_dir: Path, dive_stamp: str,
) -> list[dict]:
    """One-time bridge for pre-cyc<CC> .ts files.

    Groups by ``part<NN>`` (the only cycle proxy available pre-
    upgrade) and emits ``legacy_dive_<stamp>_on_bottom_videoNN.mp4``
    so the operator can immediately tell these came from the
    fallback path.  Numbering is a chronological 1-based index over
    the part keys; gaps in the source part numbers are squeezed out.
    """
    results: list[dict] = []
    for idx, part_key in enumerate(sorted(by_part.keys()), start=1):
        group = by_part[part_key]
        out_path = rec_dir / (
            f"legacy_dive_{dive_stamp}_on_bottom_video{idx:02d}.mp4"
        )
        src_bytes = sum((f.stat().st_size for f in group if f.exists()), 0)
        logger.info(
            "FINALIZE on_bottom legacy video %02d (part=%s, inputs=%d, "
            "%d B) -> %s",
            idx, part_key, len(group), src_bytes, out_path,
        )
        ok, msg = await _concat_phase("on_bottom", group, out_path)
        entry: dict = {
            "phase": "on_bottom",
            "kind": "legacy_cycle",
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
        # TIMELAPSE/snapshot JPEGs now live in <dive_dir>/photos/ (issue
        # #37); include it so the snapshot manifest still catalogs them.
        photos_dir = dive_dir / iprec.SNAPSHOT_SUBDIR
        if photos_dir.is_dir():
            scan_dirs.append(photos_dir)
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
        # cyc<CC>_part<NN>_<NNNNN>.ts (and the legacy
        # part<NN>_<NNNNN>.ts) both sort chronologically as plain
        # strings within one dive because the embedded counters are
        # monotonic and zero-padded.
        files.sort()
        # Bottom phase: split by mode.
        # * Continuous -> concat all .ts into a staging file then
        #   re-segment to 5-min MP4 chunks (lossless throughout).
        # * Interval   -> stitch each ipcam_start/stop cycle into one
        #   MP4 using the cyc<CC> filename tag; legacy untagged .ts
        #   files fall back to part<NN> grouping prefixed legacy_.
        # * Anything else (including bottom_mode=None from legacy
        #   callers, manual curl, or older Lua) -> historical
        #   concat-all-into-one for regression safety.
        if phase == "on_bottom" and bottom_mode == 1:
            phase_results.extend(
                await _finalize_continuous_resegment(
                    files, rec_dir, dive_stamp,
                )
            )
            continue
        if phase == "on_bottom" and bottom_mode == 2:
            phase_results.extend(
                await _finalize_interval_by_cyc(
                    files, rec_dir, dive_stamp,
                )
            )
            continue
        # Descent/ascent are recorded as a single continuous stream;
        # chunk them into 5-min MP4s like continuous bottom (issue #33)
        # so the operator gets consistent, manageable file sizes.
        if phase in ("descent", "ascent"):
            phase_results.extend(
                await _finalize_continuous_resegment(
                    files, rec_dir, dive_stamp, phase=phase,
                )
            )
            continue

        # Name from when this phase's footage actually started; fall back
        # to the dive-stamped name for legacy untagged .ts.
        recstart = _group_start_stamp(files)
        if recstart:
            out_path = rec_dir / f"{recstart}_{phase}.mp4"
        else:
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
            "rec_start": recstart,
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

    # Copy the persistent doris/dmesg logs into <dive_dir>/logs/ so the
    # diagnostic bundle rides along with the recordings (which usually
    # live on the operator's USB stick, separate from the internal log
    # dir).  Runs in an executor since it's blocking file I/O.
    logs_summary = await asyncio.get_event_loop().run_in_executor(
        None, _copy_diagnostic_logs, rec_dir,
    )

    finished_at = datetime.now(tz=timezone.utc).isoformat()
    bottom_mode_label = {
        1: "continuous",
        2: "interval",
        3: "timelapse",
    }.get(bottom_mode if bottom_mode is not None else -1, "legacy")
    manifest = {
        "dive_stamp": dive_stamp,
        "recordings_dir": str(rec_dir),
        "finalize_started_at": started_at,
        "finalize_finished_at": finished_at,
        "bottom_mode": bottom_mode_label,
        "bottom_mode_id": bottom_mode,
        "phases": phase_results,
        "snapshots": snapshot_results,
        "logs": logs_summary,
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
