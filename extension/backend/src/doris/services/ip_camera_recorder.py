"""Record IP camera RTSP streams to segmented MPEG-TS via GStreamer.

ffmpeg's RTSP demuxer is incompatible with this camera: every session is torn
down by the camera right after the first IDR/GOP, leaving ~2.7 MB stub files.
GStreamer's ``rtspsrc`` keeps the same camera connected for ~4 to 30 s before
the camera occasionally drops the session, so we wrap a ``gst-launch-1.0``
pipeline in an asyncio watchdog that restarts the pipeline on every
unexpected exit until the caller explicitly stops recording.

Pipeline (per restart):

    rtspsrc location=URL protocols=tcp is-live=true latency=2000
            retry=5 timeout=5000000 do-retransmission=false
        ! rtph264depay
        ! h264parse config-interval=-1
        ! video/x-h264,stream-format=byte-stream,alignment=au
        ! splitmuxsink location=PATTERN max-size-time=NS
                       muxer-factory=mpegtsmux send-keyframe-requests=true

The camera signals SPS/PPS only via the SDP and never re-emits them
in-band, so without ``config-interval=-1`` only the first segment after
each rtspsrc connect is decodable. ``alignment=au`` then groups SPS+PPS
+IDR into a single buffer so splitmuxsink can never split between the
parameter sets and their keyframe.

``splitmuxsink`` produces multiple ``radcam_<stamp>_part<NN>_%05d.ts``
segments per restart; ``part<NN>`` increments each time the watchdog
restarts the pipeline so files from different sessions do not collide.

Each ``gst-launch-1.0`` is started with ``-e`` so the SIGINT we send on
``stop_recording`` is converted into a clean EOS and the last segment is
finalised before the process exits.
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

IPCAM_RTSP_DIRECT = "rtsp://admin:blue@192.168.2.10:554/stream_0"

_RESTART_BACKOFF_S = 1.0
_MIN_GOOD_RUNTIME_S = 3.0
_MAX_BACKOFF_S = 5.0


_state_lock = asyncio.Lock()
_session: "RecordingSession | None" = None


def _data_root() -> Path:
    return Path(os.environ.get("DORIS_DATA_ROOT", "/tmp/storage"))


def _output_dir() -> tuple[Path, str]:
    sub = settings.ipcam_recordings_subdir.strip("/").strip()
    usb_base = usb_storage.get_recording_dir_if_available(sub)
    if usb_base is not None:
        return Path(usb_base), "usb"
    out = _data_root() / sub
    out.mkdir(parents=True, exist_ok=True)
    return out, "internal"


def _build_gst_args(rtsp_url: str, segment_s: int, pattern: str) -> list[str]:
    """gst-launch-1.0 pipeline that records ``rtsp_url`` to ``pattern``.

    ``pattern`` must contain ``%05d`` (or similar) for splitmuxsink.
    """
    seg_ns = max(1, segment_s) * 1_000_000_000

    # Forcing ``alignment=au`` ensures every access unit pushed to
    # splitmuxsink contains SPS+PPS+IDR together, so segment boundaries
    # never separate the parameter sets from the keyframe they describe.
    # Without it, only the first segment after each rtspsrc connect
    # contains valid SPS/PPS and downstream segments fail to decode
    # ("non-existing PPS 0 referenced").
    return [
        "gst-launch-1.0",
        "-e",
        "rtspsrc",
        f"location={rtsp_url}",
        "protocols=tcp",
        "is-live=true",
        "latency=2000",
        "retry=5",
        "timeout=5000000",
        "do-retransmission=false",
        "!",
        "rtph264depay",
        "!",
        "h264parse",
        "config-interval=-1",
        "!",
        "video/x-h264,stream-format=byte-stream,alignment=au",
        "!",
        "splitmuxsink",
        f"location={pattern}",
        f"max-size-time={seg_ns}",
        "muxer-factory=mpegtsmux",
        "send-keyframe-requests=true",
    ]


class RecordingSession:
    """One logical recording: keeps a gst-launch-1.0 pipeline alive across
    camera-induced disconnects until ``stop()`` is called.
    """

    def __init__(self, rtsp_url: str, segment_s: int, out_dir: Path,
                 storage_label: str, base_stamp: str) -> None:
        self._rtsp_url = rtsp_url
        self._segment_s = segment_s
        self._out_dir = out_dir
        self.storage = storage_label
        self.base_stamp = base_stamp
        self._user_stop = asyncio.Event()
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self.restart_count = 0
        self.last_pattern: str | None = None
        self.last_exit: dict | None = None

    def is_alive(self) -> bool:
        if self._task is None or self._task.done():
            return False
        return True

    def current_pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def pattern_for(self, part: int) -> str:
        return str(self._out_dir
                   / f"radcam_{self.base_stamp}_part{part:02d}_%05d.ts")

    async def _spawn_one(self, part: int) -> tuple[int, str]:
        """Start a single gst pipeline; return (returncode, pattern_used)."""
        pattern = self.pattern_for(part)
        self.last_pattern = pattern
        cmd = _build_gst_args(self._rtsp_url, self._segment_s, pattern)
        logger.info(
            "RECORD spawning gst pipeline part=%d pattern=%s url=%s",
            part, pattern, self._rtsp_url)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._proc = proc

        async def _drain() -> None:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                msg = line.decode(errors="replace").rstrip()
                if msg:
                    logger.info("gst[%d]: %s", part, msg)

        drain_task = asyncio.create_task(_drain())

        try:
            rc = await proc.wait()
        finally:
            await drain_task

        return rc, pattern

    async def _watchdog(self) -> None:
        """Run the gst pipeline; restart on unexpected exit until stop()."""
        part = 0
        backoff = _RESTART_BACKOFF_S
        while not self._user_stop.is_set():
            self.restart_count = part
            t0 = asyncio.get_event_loop().time()
            try:
                rc, pattern = await self._spawn_one(part)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("RECORD spawn failed (part=%d)", part)
                rc = -1
                pattern = self.pattern_for(part)
            runtime_s = asyncio.get_event_loop().time() - t0
            self.last_exit = {
                "part": part, "rc": rc, "runtime_s": round(runtime_s, 2),
                "pattern": pattern,
            }
            if self._user_stop.is_set():
                logger.info(
                    "RECORD pipeline part=%d exited rc=%s runtime=%.1fs (user stop)",
                    part, rc, runtime_s)
                break
            logger.warning(
                "RECORD pipeline part=%d exited rc=%s runtime=%.1fs - restarting",
                part, rc, runtime_s)
            if runtime_s >= _MIN_GOOD_RUNTIME_S:
                backoff = _RESTART_BACKOFF_S
            else:
                backoff = min(_MAX_BACKOFF_S, backoff * 1.5)
            try:
                await asyncio.wait_for(self._user_stop.wait(), timeout=backoff)
                break
            except asyncio.TimeoutError:
                pass
            part += 1
        logger.info("RECORD watchdog exiting; total parts=%d", part + 1)

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("session already started")
        self._task = asyncio.create_task(self._watchdog())

    async def stop(self, timeout_s: float = 12.0) -> None:
        """Request stop; wait for clean shutdown of current gst process."""
        self._user_stop.set()
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                logger.warning(
                    "RECORD gst-launch did not finish %.1fs after SIGINT, killing",
                    timeout_s)
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("RECORD watchdog task did not exit cleanly, cancelling")
                self._task.cancel()
                try:
                    await self._task
                except Exception:
                    pass


async def _select_rtsp_url() -> tuple[str, str]:
    """Always use the direct camera URL; MCM is intentionally not used.

    Returned tuple is ``(url, label)``; the label is logged so an
    operator can quickly see which RTSP endpoint a recording started
    against. Kept as a function (rather than a constant) to leave room
    for future fallback logic without changing call sites.
    """
    return IPCAM_RTSP_DIRECT, "direct"


async def start_recording(segment_seconds: int | None = None) -> dict:
    global _session

    seg = segment_seconds
    if seg is None:
        seg = int(settings.ipcam_segment_seconds_default)
    seg = max(1, min(seg, 86_400))

    rtsp_url, source_label = await _select_rtsp_url()

    async with _state_lock:
        if _session is not None and _session.is_alive():
            return {
                "success": False,
                "message": "Already recording",
                "recording": True,
                "base_stamp": _session.base_stamp,
                "storage": _session.storage,
            }

        out_dir, storage = _output_dir()
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        sess = RecordingSession(
            rtsp_url=rtsp_url,
            segment_s=seg,
            out_dir=out_dir,
            storage_label=storage,
            base_stamp=stamp,
        )
        sess.start()
        _session = sess
        logger.info(
            "RECORD session started base=%s storage=%s rtsp=%s seg=%ds",
            stamp, storage, source_label, seg)
        return {
            "success": True,
            "recording": True,
            "base_stamp": stamp,
            "output_directory": str(out_dir),
            "storage": storage,
            "segment_seconds": seg,
            "rtsp_source": source_label,
        }


async def stop_recording() -> dict:
    global _session
    async with _state_lock:
        sess = _session
        _session = None
        if sess is None or not sess.is_alive():
            logger.info("RECORD stop called with no active session")
            return {"success": True, "recording": False,
                    "message": "Was not recording"}
        await sess.stop()
        logger.info("RECORD stop completed; restarts=%d last_exit=%s",
                    sess.restart_count, sess.last_exit)
        return {
            "success": True,
            "recording": False,
            "base_stamp": sess.base_stamp,
            "restarts": sess.restart_count,
            "last_exit": sess.last_exit,
        }


def is_recording() -> bool:
    """Synchronous helper: True iff a recording session is currently live.

    Used by ``camera`` and ``routes/sensors`` to skip snapshot capture
    while the recorder owns the camera's RTSP session.
    """
    sess = _session
    return sess is not None and sess.is_alive()


async def recording_status() -> dict:
    sess = _session
    alive = sess is not None and sess.is_alive()
    base_stamp = sess.base_stamp if sess is not None else None
    pattern = sess.last_pattern if sess is not None else None
    restarts = sess.restart_count if sess is not None else 0
    last_exit = sess.last_exit if sess is not None else None
    pid = sess.current_pid() if (sess is not None and alive) else None
    return {
        "recording": alive,
        "base_stamp": base_stamp,
        "output_pattern": pattern,
        "pid": pid,
        "restarts": restarts,
        "last_exit": last_exit,
        "usb": usb_storage.get_status(),
    }
