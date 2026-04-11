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

logger = logging.getLogger(__name__)

# Fixed RadCam path (same host/path as BlueOS_videorecorder hauv-v2 RTSP_H265_ENDPOINT).
IPCAM_RTSP_URL = "rtsp://admin:blue@192.168.2.10:554/stream_0"

_process: asyncio.subprocess.Process | None = None
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
        "warning",
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
        "-strftime",
        "1",
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

    rtsp = IPCAM_RTSP_URL

    async with _lock:
        if _process is not None and _process.returncode is None:
            return {"success": False, "message": "Already recording", "recording": True}

        out_dir, storage = _output_dir()
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        pattern = str(out_dir / f"ipcam_{stamp}_%03d.ts")
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
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return {"success": False, "message": "ffmpeg not found", "recording": False}
        except Exception as e:
            logger.exception("ffmpeg start failed")
            return {"success": False, "message": str(e), "recording": False}

        await asyncio.sleep(0.35)
        if proc.returncode is not None:
            logger.error("ffmpeg exited immediately rc=%s", proc.returncode)
            return {
                "success": False,
                "message": f"ffmpeg exited with {proc.returncode}",
                "recording": False,
            }

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
