"""Record IP camera RTSP streams to segmented MPEG-TS via GStreamer.

The pipeline runs **in-process** via ``gi.repository.Gst`` (rather than
spawning ``gst-launch-1.0``) so the backend can:

* connect to ``splitmuxsink``'s ``format-location`` signal and compose
  filenames dynamically (required for the per-phase tag added in the
  next commit);
* fire ``split-now`` as an action signal for **zero-gap phase rotation**
  at dive-phase boundaries;
* observe pipeline errors on the GStreamer bus and restart the pipeline
  via the same watchdog logic the legacy subprocess version used.

ffmpeg's RTSP demuxer is incompatible with this camera: every session
is torn down by the camera right after the first IDR/GOP, leaving
~2.7 MB stub files.  GStreamer's ``rtspsrc`` keeps the same camera
connected for ~4 to 30 s before the camera occasionally drops the
session, so the watchdog restarts the pipeline on every unexpected
exit until the caller explicitly stops recording.

Pipeline (per instance):

    rtspsrc location=URL protocols=tcp is-live=true latency=2000
            retry=5 timeout=5000000 do-retransmission=false
        ! rtph264depay
        ! h264parse config-interval=-1
        ! video/x-h264,stream-format=byte-stream,alignment=au
        ! splitmuxsink name=muxsink max-size-time=NS
                       muxer-factory=mpegtsmux send-keyframe-requests=true
                       async-finalize=true

The camera signals SPS/PPS only via the SDP and never re-emits them
in-band, so without ``config-interval=-1`` only the first segment after
each rtspsrc connect is decodable.  ``alignment=au`` then groups
SPS+PPS+IDR into a single buffer so splitmuxsink can never split
between the parameter sets and their keyframe.

``splitmuxsink`` produces multiple ``radcam_<stamp>_part<NN>_%05d.ts``
segments per restart; ``part<NN>`` increments each time the watchdog
restarts the pipeline so files from different sessions do not collide.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402  (module-level import after require_version)

from ..config import settings
from . import usb_storage

logger = logging.getLogger(__name__)

IPCAM_RTSP_DIRECT = "rtsp://admin:blue@192.168.2.10:554/stream_0"

_RESTART_BACKOFF_S = 1.0
_MIN_GOOD_RUNTIME_S = 3.0
_MAX_BACKOFF_S = 5.0
_STOP_EOS_TIMEOUT_S = 5.0


# ── GStreamer init (idempotent; safe across threads) ───────────────────────────

_gst_init_lock = threading.Lock()
_gst_initialized = False


def _ensure_gst_init() -> None:
    global _gst_initialized
    with _gst_init_lock:
        if not _gst_initialized:
            Gst.init(None)
            _gst_initialized = True
            logger.info(
                "GStreamer initialized (version %s)", Gst.version_string(),
            )


# ── module state ───────────────────────────────────────────────────────────────

_state_lock = asyncio.Lock()
_session: "RecordingSession | None" = None
# Retained across stop_recording() so callers (e.g. /api/v1/dive/finalize)
# can resolve the most-recent dive's recordings without a second round-trip.
_last_base_stamp: str | None = None


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


def _build_pipeline_description(rtsp_url: str, segment_s: int) -> str:
    """GStreamer pipeline description matching the legacy gst-launch args.

    The muxer is named ``muxsink`` so we can fetch it after parsing and
    attach the ``format-location`` signal handler + emit ``split-now``
    for zero-gap phase rotation in the next commit.
    """
    seg_ns = max(1, segment_s) * 1_000_000_000
    return (
        f"rtspsrc location={rtsp_url} protocols=tcp is-live=true latency=2000 "
        f"retry=5 timeout=5000000 do-retransmission=false "
        f"! rtph264depay "
        f"! h264parse config-interval=-1 "
        f"! video/x-h264,stream-format=byte-stream,alignment=au "
        f"! splitmuxsink name=muxsink max-size-time={seg_ns} "
        f"muxer-factory=mpegtsmux send-keyframe-requests=true "
        f"async-finalize=true"
    )


class RecordingSession:
    """One logical recording.  Owns a GStreamer pipeline in-process and
    restarts it across camera-induced disconnects until ``stop()``.
    """

    def __init__(self, rtsp_url: str, segment_s: int, out_dir: Path,
                 storage_label: str, base_stamp: str) -> None:
        self._rtsp_url = rtsp_url
        self._segment_s = segment_s
        self._out_dir = out_dir
        self.storage = storage_label
        self.base_stamp = base_stamp
        self._user_stop = asyncio.Event()
        self._pipeline: "Gst.Pipeline | None" = None
        self._muxsink: "Gst.Element | None" = None
        self._task: asyncio.Task | None = None
        self._current_part = 0
        self.restart_count = 0
        self.last_pattern: str | None = None
        self.last_exit: dict | None = None

    def is_alive(self) -> bool:
        return self._task is not None and not self._task.done()

    def current_pid(self) -> int | None:
        # No subprocess any more; pipeline runs in the extension process.
        # Kept in the API shape for back-compat with /rec/status consumers.
        return None

    # Invoked by GStreamer on a streaming thread whenever splitmuxsink
    # opens a new fragment.  Must be fast and avoid blocking on anything
    # that needs the asyncio loop.  Only reads session-immutable state
    # (base_stamp, out_dir) plus the current part index (set before each
    # pipeline instance is started).
    def _on_format_location(self, _splitmux, fragment_id) -> str:
        path = str(
            self._out_dir
            / f"radcam_{self.base_stamp}_part{self._current_part:02d}"
            f"_{int(fragment_id):05d}.ts"
        )
        self.last_pattern = path
        return path

    def _build_pipeline(self) -> "Gst.Pipeline":
        desc = _build_pipeline_description(self._rtsp_url, self._segment_s)
        pipeline = Gst.parse_launch(desc)
        if not isinstance(pipeline, Gst.Pipeline):
            raise RuntimeError("Gst.parse_launch returned non-pipeline object")
        muxsink = pipeline.get_by_name("muxsink")
        if muxsink is None:
            raise RuntimeError("splitmuxsink 'muxsink' not found in pipeline")
        muxsink.connect("format-location", self._on_format_location)
        self._muxsink = muxsink
        return pipeline

    async def _run_one(self) -> tuple[str, float]:
        """Start the pipeline, poll its bus until ERROR/EOS or user stop.

        Returns ``(exit_reason, runtime_s)``.
        """
        t0 = asyncio.get_event_loop().time()
        logger.info(
            "RECORD spawning Gst pipeline part=%d url=%s",
            self._current_part, self._rtsp_url,
        )
        try:
            pipeline = self._build_pipeline()
        except Exception:
            logger.exception("RECORD pipeline build failed")
            return "build_failed", asyncio.get_event_loop().time() - t0

        self._pipeline = pipeline
        bus = pipeline.get_bus()

        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            logger.warning("RECORD set_state(PLAYING) returned FAILURE")
            pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
            self._muxsink = None
            return "set_state_failure", asyncio.get_event_loop().time() - t0

        exit_reason = "unknown"
        try:
            # Main loop: poll the bus for ERROR / EOS while waiting for
            # the caller to request a stop.  100ms granularity is fine
            # for our needs (phase rotation uses the split-now action
            # signal directly, not the bus).
            while not self._user_stop.is_set():
                msg = bus.timed_pop_filtered(
                    0, Gst.MessageType.ERROR | Gst.MessageType.EOS,
                )
                if msg is not None:
                    if msg.type == Gst.MessageType.ERROR:
                        err, dbg = msg.parse_error()
                        logger.warning(
                            "gst[part=%d] error: %s (%s)",
                            self._current_part, err.message, dbg,
                        )
                        exit_reason = f"error:{err.message}"
                    else:
                        logger.info(
                            "gst[part=%d] unexpected EOS",
                            self._current_part,
                        )
                        exit_reason = "unexpected_eos"
                    break
                await asyncio.sleep(0.1)

            if self._user_stop.is_set() and exit_reason == "unknown":
                # Request a clean EOS so splitmuxsink finalises the
                # last .ts file.  If the live rtspsrc pipeline can't
                # flush cleanly in time, we fall back to an immediate
                # NULL transition (same outcome as the old SIGKILL).
                logger.info(
                    "RECORD part=%d stopping (user); sending EOS",
                    self._current_part,
                )
                pipeline.send_event(Gst.Event.new_eos())
                deadline = asyncio.get_event_loop().time() + _STOP_EOS_TIMEOUT_S
                stopped = False
                while asyncio.get_event_loop().time() < deadline:
                    msg = bus.timed_pop_filtered(
                        0, Gst.MessageType.ERROR | Gst.MessageType.EOS,
                    )
                    if msg is not None and msg.type in (
                        Gst.MessageType.EOS, Gst.MessageType.ERROR,
                    ):
                        exit_reason = "stopped_clean"
                        stopped = True
                        break
                    await asyncio.sleep(0.05)
                if not stopped:
                    logger.warning(
                        "RECORD part=%d EOS not observed within %.1fs",
                        self._current_part, _STOP_EOS_TIMEOUT_S,
                    )
                    exit_reason = "stopped_forced"
        finally:
            pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
            self._muxsink = None

        return exit_reason, asyncio.get_event_loop().time() - t0

    async def _watchdog(self) -> None:
        """Run the pipeline; restart on unexpected exit until stop()."""
        backoff = _RESTART_BACKOFF_S
        while not self._user_stop.is_set():
            self.restart_count = self._current_part
            try:
                exit_reason, runtime_s = await self._run_one()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "RECORD pipeline run failed (part=%d)", self._current_part,
                )
                exit_reason, runtime_s = "exception", 0.0
            self.last_exit = {
                "part": self._current_part,
                "reason": exit_reason,
                "runtime_s": round(runtime_s, 2),
                "pattern": self.last_pattern,
            }
            if self._user_stop.is_set():
                logger.info(
                    "RECORD pipeline part=%d exited reason=%s runtime=%.1fs (user stop)",
                    self._current_part, exit_reason, runtime_s,
                )
                break
            logger.warning(
                "RECORD pipeline part=%d exited reason=%s runtime=%.1fs - restarting",
                self._current_part, exit_reason, runtime_s,
            )
            if runtime_s >= _MIN_GOOD_RUNTIME_S:
                backoff = _RESTART_BACKOFF_S
            else:
                backoff = min(_MAX_BACKOFF_S, backoff * 1.5)
            try:
                await asyncio.wait_for(self._user_stop.wait(), timeout=backoff)
                break
            except asyncio.TimeoutError:
                pass
            self._current_part += 1
        logger.info(
            "RECORD watchdog exiting; total parts=%d", self._current_part + 1,
        )

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("session already started")
        _ensure_gst_init()
        self._task = asyncio.create_task(self._watchdog())

    async def stop(self, timeout_s: float = 12.0) -> None:
        """Request stop; wait for the current pipeline instance to finalise."""
        self._user_stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=timeout_s)
            except asyncio.TimeoutError:
                logger.warning(
                    "RECORD watchdog did not exit in %.1fs, cancelling",
                    timeout_s,
                )
                self._task.cancel()
                try:
                    await self._task
                except Exception:
                    pass


async def _select_rtsp_url() -> tuple[str, str]:
    """Always use the direct camera URL; MCM is intentionally not used.

    Returned tuple is ``(url, label)``; the label is logged so an
    operator can quickly see which RTSP endpoint a recording started
    against.  Kept as a function (rather than a constant) to leave
    room for future fallback logic without changing call sites.
    """
    return IPCAM_RTSP_DIRECT, "direct"


async def start_recording(segment_seconds: int | None = None) -> dict:
    global _session, _last_base_stamp

    seg = segment_seconds
    if seg is None:
        seg = int(settings.ipcam_segment_seconds_default)
    seg = max(1, min(seg, 86_400))

    rtsp_url, source_label = await _select_rtsp_url()

    async with _state_lock:
        if _session is not None and _session.is_alive():
            # Idempotent: treat "start while already recording" as success so
            # clients (the Lua dive script in particular) can freely call
            # /rec/start on every phase transition without producing noisy
            # HTTP 400s.  The active session is unchanged.
            return {
                "success": True,
                "already_recording": True,
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
        _last_base_stamp = stamp
        logger.info(
            "RECORD session started base=%s storage=%s rtsp=%s seg=%ds",
            stamp, storage, source_label, seg,
        )
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
        logger.info(
            "RECORD stop completed; restarts=%d last_exit=%s",
            sess.restart_count, sess.last_exit,
        )
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


def last_base_stamp() -> str | None:
    """Most recent session's base_stamp, preserved across stop_recording().

    Used by ``/api/v1/dive/finalize`` to resolve the dive's files when
    the caller doesn't pass an explicit ``stamp`` query parameter.
    """
    return _last_base_stamp


async def recording_status() -> dict:
    sess = _session
    alive = sess is not None and sess.is_alive()
    base_stamp = sess.base_stamp if sess is not None else None
    pattern = sess.last_pattern if sess is not None else None
    restarts = sess.restart_count if sess is not None else 0
    last_exit = sess.last_exit if sess is not None else None
    return {
        "recording": alive,
        "base_stamp": base_stamp,
        "output_pattern": pattern,
        "pid": None,  # in-process pipeline; field kept for API back-compat
        "restarts": restarts,
        "last_exit": last_exit,
        "usb": usb_storage.get_status(),
    }
