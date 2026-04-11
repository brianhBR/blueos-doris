"""Record IP camera RTSP streams to segmented MPEG-TS via ffmpeg.

Default pipeline matches the BlueOS_videorecorder hauv-v2 idea: **remux** the incoming
compressed video (e.g. H.265 from RadCam) with ``-c:v copy`` — no decode/re-encode,
low CPU — analogous to GStreamer ``rtspsrc`` → depay/parse → mux. Optional
``DORIS_IPCAM_VIDEO_CODEC=libx264`` if you explicitly need transcoding.
"""

from __future__ import annotations

import asyncio
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
_lock = asyncio.Lock()
_last_pattern: str | None = None


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


def _build_ffmpeg_args(rtsp_url: str, segment_s: int, pattern: str) -> list[str]:
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
        # Bitstream copy: same class of work as gst depay+parse+mux — no re-encode.
        args += ["-c:v", "copy"]

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


async def start_recording(segment_seconds: int | None = None) -> dict:
    """Start ffmpeg segment recording. Each /start after /stop uses a new basename."""
    global _process, _last_pattern

    seg = segment_seconds
    if seg is None:
        seg = int(settings.ipcam_segment_seconds_default)
    seg = max(1, min(seg, 86_400))

    mcm_url = await _discover_mcm_rtsp()
    if mcm_url:
        rtsp = mcm_url
        logger.info("Using MCM RTSP relay: %s", rtsp)
    else:
        rtsp = IPCAM_RTSP_DIRECT
        logger.warning("MCM RTSP relay not found, falling back to direct camera URL")

    async with _lock:
        if _process is not None and _process.returncode is None:
            return {"success": False, "message": "Already recording", "recording": True}

        out_dir, storage = _output_dir()
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        pattern = str(out_dir / f"radcam_{stamp}_%03d.ts")
        _last_pattern = pattern

        cmd = _build_ffmpeg_args(rtsp, seg, pattern)
        logger.info(
            "Starting IP camera recorder (%s): %s",
            storage,
            " ".join(cmd[:12]) + " ... " + pattern,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return {"success": False, "message": "ffmpeg not found", "recording": False}
        except Exception as e:
            logger.exception("ffmpeg start failed")
            return {"success": False, "message": str(e), "recording": False}

        await asyncio.sleep(0.35)
        if proc.returncode is not None:
            err_bytes = await proc.stderr.read() if proc.stderr else b""
            err_text = err_bytes.decode(errors="replace").strip()
            logger.error("ffmpeg exited immediately rc=%s stderr=%s", proc.returncode, err_text)
            return {
                "success": False,
                "message": f"ffmpeg exited with {proc.returncode}: {err_text[:300]}",
                "recording": False,
            }

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

        global _stderr_task
        _stderr_task = asyncio.create_task(_drain_stderr(proc))
        _process = proc
        return {
            "success": True,
            "recording": True,
            "output_pattern": pattern,
            "output_directory": str(out_dir),
            "storage": storage,
            "segment_seconds": seg,
        }


async def stop_recording() -> dict:
    """Signal ffmpeg to finalize segments (SIGINT), then kill if needed."""
    global _process

    async with _lock:
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
        except asyncio.TimeoutExpired:
            logger.warning("ffmpeg did not exit after SIGINT, killing")
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass

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
