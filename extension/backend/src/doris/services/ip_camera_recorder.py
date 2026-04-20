"""Record IP camera RTSP streams to segmented MPEG-TS via ffmpeg.

Default pipeline matches the BlueOS_videorecorder hauv-v2 idea: **remux** the incoming
compressed video (e.g. H.265 from RadCam) with ``-c:v copy`` — no decode/re-encode,
low CPU — analogous to GStreamer ``rtspsrc`` → depay/parse → mux. Optional
``DORIS_IPCAM_VIDEO_CODEC=libx264`` if you explicitly need transcoding.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from . import usb_storage

import httpx

logger = logging.getLogger(__name__)

# Direct camera URL (fallback only).
IPCAM_RTSP_DIRECT = "rtsp://admin:blue@192.168.2.10:554/stream_0"

# MCM re-serves the camera stream via its built-in RTSP server on port 8554.
# Connecting here avoids stealing the camera's single RTSP session from MCM.
_DOCKER_HOST = os.environ.get("DORIS_BLUEOS_ADDRESS", "http://host.docker.internal")
_HOST_IP = _DOCKER_HOST.replace("http://", "").replace("https://", "").split(":")[0]
MCM_STREAMS_URL = f"{_DOCKER_HOST.rstrip('/')}:6020/streams"
MCM_RTSP_HOST = f"{_HOST_IP}:8554"


async def _discover_mcm_rtsp() -> str | None:
    """Query Mavlink Camera Manager for its local RTSP relay endpoint."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(MCM_STREAMS_URL)
            resp.raise_for_status()
            items = resp.json()
    except Exception:
        return None
    for item in items:
        endpoints = (
            item.get("video_and_stream", {})
            .get("stream_information", {})
            .get("endpoints", [])
        )
        for ep in endpoints:
            if "8554" in ep:
                import re as _re
                return _re.sub(r"rtsp://[^:/]+:8554", f"rtsp://{MCM_RTSP_HOST}", ep)
    return None


_process: asyncio.subprocess.Process | None = None
_stderr_task: asyncio.Task | None = None
_liveness_task: asyncio.Task | None = None
_lock = asyncio.Lock()
_last_pattern: str | None = None
_recording_active: bool = False

LIVENESS_TIMEOUT_S = 10
LIVENESS_MAX_RETRIES = 3


def is_recording() -> bool:
    """Thread/task-safe flag — no lock needed. Use this instead of poking _process."""
    return _recording_active


def _data_root() -> Path:
    return Path(os.environ.get("DORIS_DATA_ROOT", "/tmp/storage"))


def _output_dir() -> tuple[Path, str]:
    """Return (directory, label) with ``label`` ``usb`` or ``internal``."""
    sub = settings.ipcam_recordings_subdir.strip("/").strip()
    usb_base = usb_storage.get_recording_dir_if_available(sub)
    if usb_base is not None:
        return Path(usb_base), "usb"
    out = _data_root() / sub
    out.mkdir(parents=True, exist_ok=True)
    return out, "internal"


def _build_ffmpeg_args(rtsp_url: str, segment_s: int, pattern: str,
                       creation_time: str | None = None) -> list[str]:
    """RTSP in -> segmented MPEG-TS. ``copy`` = bitstream remux only (gst-style pass-through)."""
    vcodec = settings.ipcam_video_codec
    if vcodec not in ("copy", "libx264"):
        vcodec = "copy"

    args: list[str] = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp_url,
        "-map",
        "0:v:0",
        "-an",
    ]

    if vcodec == "libx264":
        args += [
            "-c:v",
            "libx264",
            "-preset",
            settings.ipcam_x264_preset,
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
        ]
    else:
        args += ["-c:v", "copy"]

    if creation_time:
        args += ["-metadata", f"creation_time={creation_time}"]

    args += [
        "-f",
        "segment",
        "-segment_time",
        str(max(segment_s, 1)),
        "-segment_format",
        "mpegts",
        "-reset_timestamps",
        "1",
        pattern,
    ]
    return args


async def _try_launch_ffmpeg(
    rtsp_url: str, seg: int, pattern: str, label: str,
    creation_time: str | None = None,
) -> tuple[asyncio.subprocess.Process | None, str]:
    """Spawn ffmpeg and wait briefly to confirm it stays alive.

    Returns ``(process, "")`` on success, or ``(None, error_text)`` on failure.
    """
    cmd = _build_ffmpeg_args(rtsp_url, seg, pattern, creation_time)
    logger.info("Starting IP camera recorder (%s): %s", label, " ".join(cmd[:12]) + " ... " + pattern)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None, "ffmpeg not found"
    except Exception as e:
        logger.exception("ffmpeg start failed")
        return None, str(e)

    await asyncio.sleep(0.35)
    if proc.returncode is not None:
        err_bytes = await proc.stderr.read() if proc.stderr else b""
        err_text = err_bytes.decode(errors="replace").strip()
        logger.error("ffmpeg exited immediately rc=%s url=%s stderr=%s", proc.returncode, rtsp_url, err_text[:500])
        return None, f"ffmpeg exited with {proc.returncode}: {err_text[:300]}"

    return proc, ""


async def _drain_stderr(p: asyncio.subprocess.Process) -> None:
    """Log ffmpeg stderr lines in background; log exit when it dies."""
    assert p.stderr is not None
    while True:
        line = await p.stderr.readline()
        if not line:
            break
        logger.warning("ffmpeg: %s", line.decode(errors="replace").rstrip())
    rc = await p.wait()
    if rc != 0 and rc != -2:
        logger.error("ffmpeg exited unexpectedly rc=%s", rc)


async def _kill_process(proc: asyncio.subprocess.Process) -> None:
    """Send SIGINT then escalate to SIGKILL if needed."""
    try:
        proc.send_signal(signal.SIGINT)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("ffmpeg did not exit after SIGINT, killing")
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass


def _cleanup_empty_segment(pattern: str) -> None:
    """Remove the first segment file if it's 0 bytes (stalled recording artifact)."""
    first = Path(pattern.replace("%03d", "000"))
    try:
        if first.exists() and first.stat().st_size == 0:
            first.unlink()
            logger.info("Cleaned up 0-byte stalled file: %s", first.name)
    except OSError:
        pass


MANIFEST_FILENAME = "recording_manifest.jsonl"


def _append_manifest(out_dir: Path, entry: dict) -> None:
    """Best-effort append a JSON line to the recording manifest."""
    try:
        with open(out_dir / MANIFEST_FILENAME, "a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError:
        pass


async def _liveness_monitor(pattern: str, seg: int, retries_left: int = LIVENESS_MAX_RETRIES) -> None:
    """Verify ffmpeg is actually writing video data; restart if stalled.

    MCM's RTSP relay can accept a connection (passing the 0.35s alive check)
    but stall without forwarding frames, producing a 0-byte segment file.
    This monitor detects that and restarts with a fresh RTSP discovery.
    """
    global _process, _recording_active, _stderr_task, _liveness_task, _last_pattern

    await asyncio.sleep(LIVENESS_TIMEOUT_S)

    if not _recording_active:
        return

    first_segment = Path(pattern.replace("%03d", "000"))
    try:
        size = first_segment.stat().st_size
    except FileNotFoundError:
        size = 0

    if size > 0:
        logger.info("Recording liveness OK: %s is %d bytes", first_segment.name, size)
        return

    logger.warning(
        "Recording STALLED: %s is 0 bytes after %ds — restarting (retries_left=%d)",
        first_segment.name, LIVENESS_TIMEOUT_S, retries_left,
    )

    if retries_left <= 0:
        logger.error("Recording liveness: max retries exhausted, giving up")
        async with _lock:
            if _process is not None and _process.returncode is None:
                await _kill_process(_process)
                _process = None
            _recording_active = False
        _cleanup_empty_segment(pattern)
        return

    mcm_url = await _discover_mcm_rtsp()
    rtsp_candidates: list[tuple[str, str]] = [(IPCAM_RTSP_DIRECT, "direct")]
    if mcm_url:
        rtsp_candidates.append((mcm_url, "mcm-relay"))

    async with _lock:
        if not _recording_active:
            return

        if _process is not None and _process.returncode is None:
            await _kill_process(_process)
            _process = None

        _cleanup_empty_segment(pattern)

        out_dir, storage = _output_dir()
        now = datetime.now(tz=timezone.utc)
        stamp = now.strftime("%Y%m%d_%H%M%S")
        iso_stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        new_pattern = str(out_dir / f"radcam_{stamp}_%03d.ts")

        last_err = ""
        for rtsp_url, source in rtsp_candidates:
            logger.info("Liveness restart: trying %s (%s)", source, rtsp_url)
            proc, err = await _try_launch_ffmpeg(rtsp_url, seg, new_pattern, f"{storage}/{source}", iso_stamp)
            if proc is not None:
                _stderr_task = asyncio.create_task(_drain_stderr(proc))
                _process = proc
                _last_pattern = new_pattern
                logger.info("Liveness restart succeeded via %s", source)
                _liveness_task = asyncio.create_task(
                    _liveness_monitor(new_pattern, seg, retries_left - 1)
                )
                return
            last_err = err
            logger.warning("Liveness restart: %s failed (%s)", source, err[:200])

        logger.error("Liveness restart: all RTSP sources failed, marking inactive")
        _recording_active = False


async def start_recording(segment_seconds: int | None = None) -> dict:
    """Start ffmpeg segment recording. Each /start after /stop uses a new basename."""
    global _process, _last_pattern, _liveness_task

    seg = segment_seconds
    if seg is None:
        seg = int(settings.ipcam_segment_seconds_default)
    seg = max(1, min(seg, 86_400))

    if _liveness_task is not None:
        _liveness_task.cancel()
        _liveness_task = None

    mcm_url = await _discover_mcm_rtsp()
    rtsp_candidates: list[tuple[str, str]] = [(IPCAM_RTSP_DIRECT, "direct")]
    if mcm_url:
        rtsp_candidates.append((mcm_url, "mcm-relay"))

    async with _lock:
        if _process is not None and _process.returncode is None:
            logger.info("Auto-stopping previous recording before starting new segment")
            await _kill_process(_process)
            _process = None

        out_dir, storage = _output_dir()
        now = datetime.now(tz=timezone.utc)
        stamp = now.strftime("%Y%m%d_%H%M%S")
        iso_stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        pattern = str(out_dir / f"radcam_{stamp}_%03d.ts")
        _last_pattern = pattern

        last_err = ""
        for rtsp_url, source in rtsp_candidates:
            logger.info("Trying RTSP source: %s (%s)", source, rtsp_url)
            proc, err = await _try_launch_ffmpeg(rtsp_url, seg, pattern, f"{storage}/{source}", iso_stamp)
            if proc is not None:
                break
            last_err = err
            if len(rtsp_candidates) > 1:
                logger.warning("RTSP source %s failed (%s), trying next", source, err[:200])
        else:
            return {"success": False, "message": last_err, "recording": False}

        global _stderr_task, _recording_active
        _stderr_task = asyncio.create_task(_drain_stderr(proc))
        _process = proc
        _recording_active = True
        _liveness_task = asyncio.create_task(_liveness_monitor(pattern, seg))
        _append_manifest(out_dir, {
            "event": "start",
            "time_utc": iso_stamp,
            "file": Path(pattern).name,
            "source": source,
            "codec": settings.ipcam_video_codec or "copy",
            "segment_s": seg,
            "storage": storage,
        })
        return {
            "success": True,
            "recording": True,
            "output_pattern": pattern,
            "output_directory": str(out_dir),
            "storage": storage,
            "segment_seconds": seg,
            "rtsp_source": source,
        }


async def stop_recording() -> dict:
    """Signal ffmpeg to finalize segments (SIGINT), then kill if needed."""
    global _process, _recording_active, _liveness_task

    if _liveness_task is not None:
        _liveness_task.cancel()
        _liveness_task = None

    async with _lock:
        _recording_active = False
        pattern = _last_pattern
        proc = _process
        _process = None
        if proc is None or proc.returncode is not None:
            return {"success": True, "recording": False, "message": "Was not recording"}

        try:
            proc.send_signal(signal.SIGINT)
        except ProcessLookupError:
            return {"success": True, "recording": False}

        try:
            await asyncio.wait_for(proc.wait(), timeout=12.0)
        except asyncio.TimeoutError:
            logger.warning("ffmpeg did not exit after SIGINT, killing")
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass

        if pattern:
            first = Path(pattern.replace("%03d", "000"))
            size = 0
            try:
                if first.exists():
                    size = first.stat().st_size
            except OSError:
                pass
            _append_manifest(first.parent, {
                "event": "stop",
                "time_utc": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "file": Path(pattern).name,
                "size_bytes": size,
            })

        return {"success": True, "recording": False}


async def recording_status() -> dict:
    """Return whether the recorder subprocess is alive."""
    global _process
    async with _lock:
        alive = _process is not None and _process.returncode is None
        rc = _process.returncode if _process is not None else None
        return {
            "recording": alive,
            "returncode": rc,
            "output_pattern": _last_pattern,
            "usb": usb_storage.get_status(),
        }
