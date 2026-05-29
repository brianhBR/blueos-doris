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

``splitmuxsink`` produces multiple
``radcam_<stamp>_<phase>_cyc<CC>_part<NN>_%05d.ts`` segments per
session.  Two distinct counters are embedded in the filename:

* ``cyc<CC>`` increments **once per ``start_recording`` call** within
  one dive.  Every ``.ts`` file written by a single ipcam_start /
  ipcam_stop cycle therefore carries the same ``<CC>``, regardless of
  how many times the watchdog rebuilt the pipeline mid-cycle (camera
  RTSP teardowns, transient errors, anything).  Finalize uses
  ``<CC>`` alone to identify a logical interval cycle -- no
  timing/mtime heuristics.
* ``part<NN>`` increments on every pipeline rebuild (initial start +
  each watchdog restart) so file paths are unique even when ``<CC>``
  is repeated across rebuilds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402  (module-level import after require_version)

from ..config import settings
from . import usb_storage

logger = logging.getLogger(__name__)

_IPCAM_RTSP_DEFAULT = "rtsp://admin:blue@192.168.2.10:554/stream_0"
IPCAM_RTSP_DIRECT = os.environ.get(
    "DORIS_IPCAM_RTSP_URL", _IPCAM_RTSP_DEFAULT,
)

_RESTART_BACKOFF_S = 1.0
_MIN_GOOD_RUNTIME_S = 3.0
_MAX_BACKOFF_S = 5.0
_STOP_EOS_TIMEOUT_S = 5.0

# Phase labels are embedded into filenames; keep them path-safe.
# Accepted: lowercase alphanumerics + underscore, 1..32 chars.
_PHASE_RE = re.compile(r"^[a-z0-9_]{1,32}$")
PHASE_DEFAULT = "manual"


def sanitize_phase(raw: str | None) -> str:
    """Return ``raw`` if it's a valid phase label, else ``PHASE_DEFAULT``.

    The label is embedded verbatim into on-disk filenames so it must
    be restricted to path-safe characters.  Anything the caller sends
    that doesn't match the whitelist falls back to ``manual`` (the
    same label used for non-dive UI-initiated recordings).
    """
    if raw is None:
        return PHASE_DEFAULT
    s = str(raw).strip().lower()
    if not s or not _PHASE_RE.match(s):
        return PHASE_DEFAULT
    return s


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
# Dive-scoped base stamp.  All recording sessions (and TIMELAPSE-mode
# snapshots) within a single dive reuse this stamp so every .ts / .jpg
# carries the same ``radcam_<stamp>_...`` prefix.  That lets
# ``finalize_dive`` group files across phase boundaries even when the
# Lua script stops and restarts the recorder between phases (e.g.
# descent -> on_bottom in VIDEO_INTERVAL mode, or the recorder's own
# watchdog restarts after a camera-induced RTSP tear-down).
#
# Allocated lazily by the first start_recording / snapshot of a dive,
# cleared by ``/api/v1/dive/finalize`` (via ``clear_snapshot_state``).
_last_base_stamp: str | None = None

# Per-(stamp, phase) JPEG sequence counters for TIMELAPSE-mode snapshots.
# Cleared on dive-finalize so each new dive starts fresh.
_snapshot_seqs: dict[tuple[str, str], int] = {}

# Next part index any new RecordingSession should claim for the
# current dive stamp.  VIDEO_INTERVAL mode stops the recorder during
# pauses and starts a new session for each record cycle; without this
# offset every session would start at part00 and clobber the previous
# session's .ts files (splitmuxsink's fragment_id also restarts at 0
# in each pipeline instance).  Bumped past the last-used part on
# stop_recording, reset to 0 by clear_snapshot_state.
_session_part_offset: int = 0

# Sequence counter for ``ipcam_start / ipcam_stop`` cycles within a
# dive.  Incremented once per ``start_recording`` call (the
# non-idempotent path that actually constructs a new
# RecordingSession); idempotent re-uses do NOT bump it.  The current
# value is stamped into every ``.ts`` filename as ``cyc<CC>``, so
# every fragment written by a single ipcam_start / ipcam_stop cycle
# (including all fragments produced by mid-cycle watchdog rebuilds)
# shares the same ``<CC>`` and finalize can group them into one MP4
# without any timing heuristic.  Reset to 0 by clear_snapshot_state
# so each fresh dive starts at cyc01.
_dive_cycle_seq: int = 0

# Direct camera snapshot endpoint.  This is a raw HTTP path on the
# IP camera itself (NOT a MCM-mediated path) so that the timelapse
# fast path is independent of mavlink-camera-manager state.  The
# RadCam (firmware 18.010.U35.5) serves a fresh 4K JPEG at this URL
# with no auth required, in ~200ms; resolution and quality are
# whatever the operator set on the camera's "snap" page (see
# http://<cam>:80/index.html#/cameraConf -> Video and Audio -> Snap).
#
# Override with the ``DORIS_IPCAM_SNAPSHOT_URL`` env var for testing
# or other camera firmware.  Default is derived from the RTSP host
# of ``IPCAM_RTSP_DIRECT`` so changing the camera IP in a single
# place keeps both endpoints in sync.
def _default_snapshot_url() -> str:
    """Build ``http://<rtsp_host>/cgi-bin/onesnap.cgi`` from IPCAM_RTSP_DIRECT."""
    from urllib.parse import urlsplit
    parts = urlsplit(IPCAM_RTSP_DIRECT)
    host = parts.hostname or "192.168.2.10"
    return f"http://{host}/cgi-bin/onesnap.cgi"


IPCAM_SNAPSHOT_URL = os.environ.get(
    "DORIS_IPCAM_SNAPSHOT_URL", _default_snapshot_url(),
)
_SNAPSHOT_TIMEOUT_S = 5.0


def _data_root() -> Path:
    return Path(os.environ.get("DORIS_DATA_ROOT", "/tmp/storage"))


def _output_dir() -> tuple[Path, str]:
    """Return the recordings root + storage label (``"usb"`` or ``"internal"``).

    Callers should NOT write files directly here: per-dive output now
    lives inside ``<root>/dive_<stamp>/`` (see :func:`_dive_dir` below)
    so a single dive's .ts segments, snapshots, finalised MP4s, and
    manifest are colocated and easy to keep / move / delete as a unit.
    """
    sub = settings.ipcam_recordings_subdir.strip("/").strip()
    usb_base = usb_storage.get_recording_dir_if_available(sub)
    if usb_base is not None:
        return Path(usb_base), "usb"
    out = _data_root() / sub
    out.mkdir(parents=True, exist_ok=True)
    return out, "internal"


def _dive_dir(root: Path, stamp: str) -> Path:
    """``<root>/dive_<stamp>/``, created on demand.

    All artifacts for a single dive (descent/on_bottom/ascent .ts
    segments, TIMELAPSE .jpg snapshots, finalized .mp4 outputs, and
    the manifest.json) live inside this folder so the data tab in
    the UI doesn't accumulate one giant flat list across many dives.
    """
    d = root / f"dive_{stamp}"
    d.mkdir(parents=True, exist_ok=True)
    return d


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
        f"! h264parse name=h264parse config-interval=-1 "
        f"! video/x-h264,stream-format=byte-stream,alignment=au "
        f"! splitmuxsink name=muxsink max-size-time={seg_ns} "
        f"muxer-factory=mpegtsmux send-keyframe-requests=true "
        f"async-finalize=true"
    )


def _caps_to_dict(caps) -> dict | None:
    """Flatten the negotiated H.264 caps into a small JSON-able dict.

    Returns ``None`` if caps are absent/empty.  Best-effort: any GI
    quirk just drops the offending field rather than raising into the
    streaming thread.
    """
    try:
        if caps is None or caps.get_size() == 0:
            return None
        s = caps.get_structure(0)
        out: dict = {"name": s.get_name()}
        ok, w = s.get_int("width")
        if ok:
            out["width"] = w
        ok, h = s.get_int("height")
        if ok:
            out["height"] = h
        ok, num, den = s.get_fraction("framerate")
        if ok and den:
            out["framerate"] = round(num / den, 3)
        for key in ("profile", "level", "stream-format", "alignment"):
            val = s.get_string(key)
            if val:
                out[key] = val
        return out
    except Exception:
        return None


class _StreamLog:
    """Append-only JSONL log of recorder stream + cadence events for one
    dive.

    Written next to the recordings (``<dive_dir>/stream_log.jsonl``) so
    it survives the 12 MB ``doris.log`` rotation window and is trivially
    parseable for interval-spacing analysis: ``session_start`` /
    ``first_frame`` / ``fragment_open`` / ``pipeline_exit`` /
    ``session_stop`` events each carry wall-clock + monotonic stamps, so
    the true gap between clips (pause vs. reconnect latency) can be
    reconstructed offline.

    Thread-safe: events arrive from both the asyncio event loop and the
    GStreamer streaming threads.  Strictly best-effort -- a write failure
    is swallowed so logging can never disrupt recording.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def emit(self, event: str, **fields) -> None:
        rec: dict = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "mono": round(time.monotonic(), 3),
            "event": event,
        }
        rec.update(fields)
        try:
            line = json.dumps(rec, default=str)
        except Exception:
            return
        try:
            with self._lock, open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            logger.debug("STREAM log write failed (%s)", self._path, exc_info=True)
        # Mirror to doris.log so a live tail shows the same timeline.
        logger.info("STREAM %s %s", event, fields)


class RecordingSession:
    """One logical recording.  Owns a GStreamer pipeline in-process and
    restarts it across camera-induced disconnects until ``stop()``.
    """

    def __init__(self, rtsp_url: str, segment_s: int, out_dir: Path,
                 storage_label: str, base_stamp: str,
                 phase: str = PHASE_DEFAULT,
                 initial_part: int = 0,
                 cycle_seq: int = 0,
                 stream_log: "_StreamLog | None" = None) -> None:
        self._rtsp_url = rtsp_url
        self._segment_s = segment_s
        self._out_dir = out_dir
        self.storage = storage_label
        self.base_stamp = base_stamp
        # current_phase is read by the format-location callback on the
        # GStreamer streaming thread.  Python attribute assignment is
        # GIL-protected; no explicit lock needed.  Writes happen from
        # the asyncio event loop (start_recording / rotate_to_phase).
        self.current_phase: str = sanitize_phase(phase)
        self._user_stop = asyncio.Event()
        self._pipeline: "Gst.Pipeline | None" = None
        self._muxsink: "Gst.Element | None" = None
        self._task: asyncio.Task | None = None
        # initial_part is the part index this session starts at; the
        # caller (module-level start_recording) supplies a value past
        # the last part used by the prior session for the same
        # base_stamp so VIDEO_INTERVAL-mode start/stop cycles don't
        # collide on _part00_00000.ts.
        self._initial_part = max(0, int(initial_part))
        self._current_part = self._initial_part
        # cycle_seq stays constant for the whole life of this session
        # (start -> watchdog rebuilds -> stop) and is stamped into
        # every .ts filename as cyc<CC>.  Finalize groups bottom .ts
        # by cyc<CC> to stitch each ipcam_start / ipcam_stop cycle
        # into a single MP4.
        self.cycle_seq = max(0, int(cycle_seq))
        self.restart_count = 0
        # First-frame instrumentation.  ``session_started_at`` is the
        # monotonic time the watchdog task was launched; ``first_frame_at``
        # is the monotonic time splitmuxsink opened its first fragment
        # (i.e. the first H.264 access unit actually hit disk).  The
        # delta is the RTSP connect + jitter-buffer-fill latency that
        # VIDEO_INTERVAL mode would otherwise silently charge against the
        # operator's record window.  Set once (on the GStreamer streaming
        # thread, GIL-safe) and read by recording_status() so the Lua
        # dive script can gate its record clock on real frames.
        self.session_started_at: float | None = None
        self.first_frame_at: float | None = None
        # Per-dive structured stream/cadence log (best-effort, may be
        # None).  ``frame_count`` accumulates across watchdog rebuilds
        # within this cycle; ``stream_caps`` is the negotiated
        # resolution/framerate/profile captured on the first buffer.
        self._stream_log = stream_log
        self.frame_count = 0
        self.stream_caps: dict | None = None
        self.last_pattern: str | None = None
        self.last_exit: dict | None = None
        self.rotation_count = 0
        self._last_phases: list[str] = [self.current_phase]
        # Pending phase queued when a rotate lands while the underlying
        # pipeline is not-yet-ready (between watchdog restarts, muxsink
        # already torn down in _run_one's finally block).  Applied at
        # the start of the next pipeline iteration so the first segment
        # of that part carries the requested phase.
        self._pending_phase: str | None = None

    def is_alive(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def frames_flowing(self) -> bool:
        """True once the first .ts fragment has been opened (frames on disk)."""
        return self.first_frame_at is not None

    def first_frame_latency_s(self) -> float | None:
        """Seconds from watchdog launch to the first frame, or None if pending."""
        if self.first_frame_at is None or self.session_started_at is None:
            return None
        return round(self.first_frame_at - self.session_started_at, 3)

    def current_pid(self) -> int | None:
        # No subprocess any more; pipeline runs in the extension process.
        # Kept in the API shape for back-compat with /rec/status consumers.
        return None

    def _emit(self, event: str, **fields) -> None:
        """Emit a stream-log event tagged with this session's part/cyc."""
        sl = self._stream_log
        if sl is not None:
            sl.emit(event, part=self._current_part, cyc=self.cycle_seq, **fields)

    def _on_h264_buffer(self, _pad, _info):
        """Buffer probe on h264parse src; runs on the streaming thread.

        Cheap on the steady-state path (just a counter bump).  On the
        very first buffer it latches the precise first-frame time and the
        negotiated caps (resolution / framerate / H.264 profile+level)
        which are otherwise never recorded, and emits a ``first_frame``
        event carrying the connect latency.
        """
        self.frame_count += 1
        if self.first_frame_at is None:
            self.first_frame_at = time.monotonic()
            try:
                self.stream_caps = _caps_to_dict(_pad.get_current_caps())
            except Exception:
                self.stream_caps = None
            self._emit(
                "first_frame",
                latency_s=self.first_frame_latency_s(),
                caps=self.stream_caps,
            )
        return Gst.PadProbeReturn.OK

    # Invoked by GStreamer on a streaming thread whenever splitmuxsink
    # opens a new fragment.  Must be fast and avoid blocking on anything
    # that needs the asyncio loop.  Reads ``current_phase`` at the
    # moment the new file is opened -- so after rotate_to_phase updates
    # current_phase and fires split-now, the next callback (triggered
    # at the next keyframe) produces a file labelled with the new phase.
    def _on_format_location(self, _splitmux, fragment_id) -> str:
        # The first fragment opened by this session marks the moment
        # real video started landing on disk.  Latch it once; later
        # fragments (segment rotations, watchdog rebuilds) leave it
        # untouched so the recorded latency reflects the original
        # connect, not subsequent splits.
        if self.first_frame_at is None:
            self.first_frame_at = time.monotonic()
        phase = self.current_phase
        path = str(
            self._out_dir
            / f"radcam_{self.base_stamp}_{phase}_cyc{self.cycle_seq:02d}"
            f"_part{self._current_part:02d}_{int(fragment_id):05d}.ts"
        )
        self.last_pattern = path
        self._emit(
            "fragment_open", fragment_id=int(fragment_id), phase=phase,
            path=path,
        )
        return path

    async def rotate_to_phase(
        self, new_phase: str, segment_seconds: int | None = None,
    ) -> dict:
        """Rotate to ``new_phase``.  Zero-gap when the pipeline is live,
        queued otherwise so the next pipeline part picks it up.

        ``segment_seconds`` (if given) updates the live splitmuxsink's
        ``max-size-time`` so subsequent fragments rotate at the new
        boundary.  Used by the Lua dive script to clamp the bottom
        phase to 5-minute chunks in CONTINUOUS mode (DORIS_BTM_CMOD=1)
        and to restore the larger default for ascent.  The new value
        is also stored on the session so a watchdog-driven pipeline
        rebuild keeps the same segment policy.

        Three code paths:

        1. Pipeline live + muxsink ready  ->  update current_phase,
           emit ``split-now``; the current .ts closes on the next
           keyframe (<= one GOP) and the next .ts opens with the new
           phase in its filename.  This is the intended zero-gap path.
        2. Session alive but pipeline mid-restart (muxsink torn down in
           _run_one's finally block, watchdog in backoff)  ->  queue
           the phase in ``_pending_phase``.  The next ``_run_one``
           iteration applies it before the first format-location
           callback fires, so the first .ts of that part carries the
           new phase.  Reports ``queued: True`` so the caller can tell.
        3. Session not alive (watchdog exited, e.g. post-stop)  ->
           return ``{"success": False, "reason": "not_recording"}``.
        """
        new_phase = sanitize_phase(new_phase)
        if not self.is_alive():
            return {"success": False, "reason": "not_recording"}

        # Clamp + persist the new segment policy so a watchdog rebuild
        # picks it up too.  Applied to the live muxsink below; the
        # mid-restart branch just persists.
        new_segment_s: int | None = None
        if segment_seconds is not None:
            try:
                new_segment_s = max(1, min(int(segment_seconds), 86_400))
            except (TypeError, ValueError):
                new_segment_s = None
        if new_segment_s is not None:
            self._segment_s = new_segment_s

        old_phase = self.current_phase
        muxsink = self._muxsink
        if muxsink is None:
            # Pipeline mid-restart -- queue so the next iteration picks
            # it up in _run_one before the first callback fires.
            self._pending_phase = new_phase
            logger.info(
                "RECORD rotate queued: %s -> %s segment_s=%s "
                "(pipeline mid-restart)",
                old_phase, new_phase, new_segment_s,
            )
            return {
                "success": True, "from_phase": old_phase,
                "to_phase": new_phase, "rotated": False,
                "queued": True,
                "segment_seconds": self._segment_s,
            }
        if new_phase == old_phase and new_segment_s is None:
            return {
                "success": True, "from_phase": old_phase,
                "to_phase": new_phase, "rotated": False,
                "note": "already in requested phase",
                "segment_seconds": self._segment_s,
            }
        # Update the live variable BEFORE emitting split-now so the next
        # format-location callback picks up the new label.  GIL-safe.
        self.current_phase = new_phase
        if new_segment_s is not None:
            # splitmuxsink.max-size-time is a runtime-writable GObject
            # property; setting it before split-now means the new value
            # governs every fragment after this rotation.
            try:
                muxsink.set_property(
                    "max-size-time", int(new_segment_s) * 1_000_000_000,
                )
            except Exception:
                logger.exception(
                    "RECORD failed to update splitmuxsink max-size-time "
                    "(continuing with prior value)"
                )
        if new_phase != old_phase:
            self.rotation_count += 1
            self._last_phases.append(new_phase)
        logger.info(
            "RECORD rotate: %s -> %s segment_s=%d (rotations=%d)",
            old_phase, new_phase, self._segment_s, self.rotation_count,
        )
        try:
            muxsink.emit("split-now")
        except Exception:
            logger.exception("RECORD split-now emit failed")
            return {"success": False, "reason": "split_now_failed"}
        return {
            "success": True, "from_phase": old_phase,
            "to_phase": new_phase, "rotated": True,
            "rotation_count": self.rotation_count,
            "segment_seconds": self._segment_s,
        }

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
        # Buffer probe on h264parse's src pad: counts frames and captures
        # first-frame timing + negotiated caps for the stream log.  Best-
        # effort -- a missing element or pad must never fail the build.
        try:
            h264 = pipeline.get_by_name("h264parse")
            srcpad = h264.get_static_pad("src") if h264 is not None else None
            if srcpad is not None:
                srcpad.add_probe(Gst.PadProbeType.BUFFER, self._on_h264_buffer)
        except Exception:
            logger.debug("RECORD failed to attach h264 buffer probe", exc_info=True)
        return pipeline

    async def _run_one(self) -> tuple[str, float]:
        """Start the pipeline, poll its bus until ERROR/EOS or user stop.

        Returns ``(exit_reason, runtime_s)``.
        """
        t0 = asyncio.get_event_loop().time()
        # If a rotate landed while the previous pipeline was tearing
        # down, apply the queued phase now so the first fragment of
        # this new part carries the requested label.
        if self._pending_phase is not None and self._pending_phase != self.current_phase:
            old_phase = self.current_phase
            self.current_phase = self._pending_phase
            self.rotation_count += 1
            self._last_phases.append(self.current_phase)
            logger.info(
                "RECORD rotate applied on restart: %s -> %s (rotations=%d)",
                old_phase, self.current_phase, self.rotation_count,
            )
        self._pending_phase = None
        logger.info(
            "RECORD spawning Gst pipeline part=%d url=%s phase=%s",
            self._current_part, self._rtsp_url, self.current_phase,
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

        self._emit("pipeline_playing")

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
            self._emit(
                "pipeline_exit", reason=exit_reason,
                runtime_s=round(runtime_s, 2), frames=self.frame_count,
                user_stop=self._user_stop.is_set(),
            )
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
            self._emit("pipeline_restart", backoff_s=round(backoff, 2))
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
        self.session_started_at = time.monotonic()
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


async def start_recording(
    segment_seconds: int | None = None,
    phase: str | None = None,
) -> dict:
    """Start (or re-use) a recording session.

    ``phase`` tags every ``.ts`` segment produced by this session until
    :func:`rotate_to_phase` is called; defaults to ``"manual"`` for UI-
    initiated recordings outside of a dive.
    """
    global _session, _last_base_stamp, _dive_cycle_seq

    seg = segment_seconds
    if seg is None:
        seg = int(settings.ipcam_segment_seconds_default)
    seg = max(1, min(seg, 86_400))

    phase_label = sanitize_phase(phase)

    rtsp_url, source_label = await _select_rtsp_url()

    async with _state_lock:
        if _session is not None and _session.is_alive():
            # Idempotent: treat "start while already recording" as success so
            # clients (the Lua dive script in particular) can freely call
            # /rec/start on every phase transition without producing noisy
            # HTTP 400s.  The active session is unchanged; if the caller
            # passed a non-default phase different from the current one we
            # transparently rotate so they get the expected labelling.
            result = {
                "success": True,
                "already_recording": True,
                "message": "Already recording",
                "recording": True,
                "base_stamp": _session.base_stamp,
                "storage": _session.storage,
                "phase": _session.current_phase,
            }
            # If the caller passed a segment override, fold it through
            # the rotate path so a /rec/start while-already-recording
            # can also retune splitmuxsink.  Passing None preserves the
            # current segment policy.
            if phase is not None and phase_label != _session.current_phase:
                rot = await _session.rotate_to_phase(
                    phase_label, segment_seconds=segment_seconds,
                )
                result["rotate"] = rot
                result["phase"] = _session.current_phase
            elif segment_seconds is not None:
                # Same phase but a segment retune was requested.  Apply
                # via rotate_to_phase (idempotent on the phase label,
                # writes the live splitmuxsink property).
                rot = await _session.rotate_to_phase(
                    _session.current_phase, segment_seconds=segment_seconds,
                )
                result["rotate"] = rot
            return result

        out_root, storage = _output_dir()
        # Reuse the dive-scoped stamp if one already exists so a
        # descent -> (stop) -> on_bottom transition (or a finalize-less
        # restart) keeps writing under the same radcam_<stamp>_... prefix.
        # Only finalize clears _last_base_stamp.
        stamp = _last_base_stamp or datetime.now(tz=timezone.utc).strftime(
            "%Y%m%d_%H%M%S",
        )
        # All output for this dive (segments, snapshots, finalized MP4s,
        # and the manifest) goes into <root>/dive_<stamp>/ so each dive
        # is isolated in its own folder.  Created on demand.
        out_dir = _dive_dir(out_root, stamp)
        # Claim a part-index range past anything the prior session for
        # this stamp already wrote.  Without this, splitmuxsink's
        # internal fragment_id (which also restarts at 0) plus our
        # _current_part both reset, and a stop -> start cycle would
        # overwrite the previous cycle's _part00_00000.ts.
        initial_part = _session_part_offset
        # Bump the dive-scoped cycle counter so this ipcam_start/stop
        # cycle gets its own ``cyc<CC>`` tag, distinct from any prior
        # cycle in the same dive.  All .ts files this session writes
        # (including those produced by mid-cycle watchdog rebuilds)
        # share this number, so finalize can stitch them losslessly
        # into one MP4 per cycle without any timing heuristic.
        _dive_cycle_seq += 1
        cycle_seq = _dive_cycle_seq
        # One stream log per dive, colocated with the recordings and
        # appended across every cycle so the whole interval cadence
        # lands in a single parseable file.
        stream_log = _StreamLog(out_dir / "stream_log.jsonl")
        sess = RecordingSession(
            rtsp_url=rtsp_url,
            segment_s=seg,
            out_dir=out_dir,
            storage_label=storage,
            base_stamp=stamp,
            phase=phase_label,
            initial_part=initial_part,
            cycle_seq=cycle_seq,
            stream_log=stream_log,
        )
        sess.start()
        _session = sess
        _last_base_stamp = stamp
        stream_log.emit(
            "session_start", cyc=cycle_seq, phase=phase_label,
            part0=initial_part, seg_s=seg, storage=storage,
            rtsp_source=source_label, base_stamp=stamp,
        )
        logger.info(
            "RECORD session started base=%s phase=%s cyc=%02d storage=%s "
            "rtsp=%s seg=%ds",
            stamp, phase_label, cycle_seq, storage, source_label, seg,
        )
        return {
            "success": True,
            "recording": True,
            "base_stamp": stamp,
            "phase": phase_label,
            "cycle_seq": cycle_seq,
            "output_directory": str(out_dir),
            "storage": storage,
            "segment_seconds": seg,
            "rtsp_source": source_label,
        }


async def rotate_to_phase(
    new_phase: str | None, segment_seconds: int | None = None,
) -> dict:
    """Module-level helper mirroring :meth:`RecordingSession.rotate_to_phase`.

    ``segment_seconds`` (when supplied) updates the live splitmuxsink's
    max segment time so the Lua can switch to 5-minute chunking on the
    descent->bottom rotation in CONTINUOUS mode and restore the larger
    default on the bottom->ascent rotation.

    Returns ``{"success": False, "reason": "not_recording"}`` if no
    session is currently live.  This is the entry point wired to the
    ``POST /rec/rotate`` HTTP route used by the Lua dive script.
    """
    new_phase_label = sanitize_phase(new_phase)
    sess = _session
    if sess is None or not sess.is_alive():
        return {
            "success": False, "reason": "not_recording",
            "to_phase": new_phase_label,
        }
    return await sess.rotate_to_phase(
        new_phase_label, segment_seconds=segment_seconds,
    )


async def stop_recording() -> dict:
    global _session, _session_part_offset
    async with _state_lock:
        sess = _session
        _session = None
        if sess is None or not sess.is_alive():
            logger.info("RECORD stop called with no active session")
            return {"success": True, "recording": False,
                    "message": "Was not recording"}
        await sess.stop()
        # Reserve every part index this session used (initial_part
        # through _current_part) so the next session in the same dive
        # stamp picks up where this one left off.  Without this bump,
        # the next start_recording would re-use part00 and clobber the
        # files this session just produced.
        _session_part_offset = sess._current_part + 1
        ff_latency = sess.first_frame_latency_s()
        if sess._stream_log is not None:
            sess._stream_log.emit(
                "session_stop", cyc=sess.cycle_seq,
                restarts=sess.restart_count, rotations=sess.rotation_count,
                first_frame_latency_s=ff_latency, frames=sess.frame_count,
                caps=sess.stream_caps, last_exit=sess.last_exit,
            )
        logger.info(
            "RECORD stop completed; restarts=%d rotations=%d "
            "first_frame_latency_s=%s frames=%d last_exit=%s next_part_offset=%d",
            sess.restart_count, sess.rotation_count, ff_latency,
            sess.frame_count, sess.last_exit, _session_part_offset,
        )
        return {
            "success": True,
            "recording": False,
            "base_stamp": sess.base_stamp,
            "restarts": sess.restart_count,
            "rotations": sess.rotation_count,
            "first_frame_latency_s": ff_latency,
            "phases": list(sess._last_phases),
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


def _ensure_dive_stamp() -> str:
    """Return the current dive's base_stamp, allocating one if missing.

    TIMELAPSE mode calls ``/rec/snapshot`` without ever starting a
    recording session, so ``_last_base_stamp`` may be unset when the
    first snapshot of a dive is taken.  Allocate one on demand and
    reuse it for all snapshots in the same dive (until finalize clears
    it).
    """
    global _last_base_stamp
    if _last_base_stamp is None:
        _last_base_stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _last_base_stamp


def _next_snapshot_seq(stamp: str, phase: str) -> int:
    key = (stamp, phase)
    seq = _snapshot_seqs.get(key, 0) + 1
    _snapshot_seqs[key] = seq
    return seq


def clear_snapshot_state() -> None:
    """Reset snapshot counters, dive-scoped stamp, part offset, and cycle seq.

    Called by ``/api/v1/dive/finalize`` so the next dive allocates a
    fresh ``_last_base_stamp`` on its first start_recording / snapshot,
    snapshot seq counters start at 1 again, the next session starts
    at part00 instead of inheriting the prior dive's offset, and the
    next session's first ``ipcam_start`` produces ``cyc01`` rather
    than continuing the prior dive's cycle counter.
    """
    global _last_base_stamp, _session_part_offset, _dive_cycle_seq
    _snapshot_seqs.clear()
    _last_base_stamp = None
    _session_part_offset = 0
    _dive_cycle_seq = 0


async def fetch_camera_jpeg() -> tuple[bytes | None, str]:
    """GET a single JPEG straight from the IP camera at IPCAM_SNAPSHOT_URL.

    No MCM, no auth, no ffmpeg.  Just a plain HTTP GET against the
    camera's built-in snapshot CGI.  The RadCam returns a fresh 4K
    JPEG (matching the operator-configured "snap" resolution and
    quality) in roughly 200 ms with no authentication required.
    Times out after :data:`_SNAPSHOT_TIMEOUT_S` seconds and tolerates
    minor connection errors by returning ``(None, message)`` so the
    caller can decide what to do.

    Public helper -- used by both :func:`take_phase_snapshot` (which
    writes the JPEG to a per-dive folder) and the sensor-page preview
    route (which streams the JPEG straight back to the browser).
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=_SNAPSHOT_TIMEOUT_S) as client:
            resp = await client.get(IPCAM_SNAPSHOT_URL)
    except Exception as e:
        return None, f"http_error: {e}"
    if resp.status_code != 200:
        return None, f"http_status_{resp.status_code}"
    ctype = resp.headers.get("content-type", "")
    if not ctype.startswith("image"):
        return None, f"unexpected_content_type: {ctype!r}"
    if not resp.content or not resp.content.startswith(b"\xff\xd8"):
        return None, "not_jpeg_magic"
    return resp.content, "ok"


# Backwards-compatible private alias for in-module callers; new code
# should use the public name above.
_fetch_camera_jpeg = fetch_camera_jpeg


async def take_phase_snapshot(phase: str | None) -> dict:
    """Capture a single JPEG from the RTSP camera and save it under
    ``<recordings>/radcam_<stamp>_<phase>_<seq>.jpg``.

    This is the TIMELAPSE primitive driven by the Lua dive script.
    Returns ``success=False, reason="recorder_active"`` (with an HTTP
    status hint of 409) when the recorder pipeline is alive -- the
    camera only supports one RTSP session at a time, so snapshot and
    recording are mutually exclusive.

    The Lua is expected to call :func:`stop_recording` first if it
    wants to take snapshots during a previously-recording phase.
    """
    phase_label = sanitize_phase(phase)
    if is_recording():
        return {
            "success": False,
            "reason": "recorder_active",
            "message": (
                "Cannot take a snapshot while the IP camera recorder is "
                "active (camera allows only one RTSP session)"
            ),
            "http_status": 409,
        }

    out_root, storage = _output_dir()
    stamp = _ensure_dive_stamp()
    out_dir = _dive_dir(out_root, stamp)
    seq = _next_snapshot_seq(stamp, phase_label)
    filename = f"radcam_{stamp}_{phase_label}_{seq:05d}.jpg"
    path = out_dir / filename

    jpeg, reason = await _fetch_camera_jpeg()
    if jpeg is None:
        logger.warning("SNAPSHOT fetch failed (%s) url=%s",
                       reason, IPCAM_SNAPSHOT_URL)
        return {
            "success": False, "reason": "snapshot_failed",
            "message": f"Camera snapshot failed: {reason}",
            "url": IPCAM_SNAPSHOT_URL,
            "http_status": 502,
        }

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(jpeg)
    except Exception as e:
        logger.exception("SNAPSHOT write failed: %s", path)
        return {
            "success": False, "reason": "write_failed",
            "message": str(e), "http_status": 500,
        }

    logger.info(
        "SNAPSHOT saved %s (%d bytes, phase=%s, seq=%d)",
        path, len(jpeg), phase_label, seq,
    )
    return {
        "success": True,
        "path": str(path),
        "filename": filename,
        "base_stamp": stamp,
        "phase": phase_label,
        "seq": seq,
        "size_bytes": len(jpeg),
        "storage": storage,
    }


async def recording_status() -> dict:
    sess = _session
    alive = sess is not None and sess.is_alive()
    base_stamp = sess.base_stamp if sess is not None else None
    pattern = sess.last_pattern if sess is not None else None
    restarts = sess.restart_count if sess is not None else 0
    last_exit = sess.last_exit if sess is not None else None
    phase = sess.current_phase if sess is not None else None
    rotations = sess.rotation_count if sess is not None else 0
    # ``frames_flowing`` is the signal the Lua dive script gates its
    # VIDEO_INTERVAL record clock on: only count the record window once
    # real video is landing on disk, so a slow RTSP connect doesn't
    # shorten the clip.  Reported even when not alive (False) so the
    # poller gets a definite answer.  Kept near the top of the dict so
    # it lands in the first TCP segment the Lua reader pulls.
    frames_flowing = bool(sess.frames_flowing) if sess is not None else False
    ff_latency = sess.first_frame_latency_s() if sess is not None else None
    frame_count = sess.frame_count if sess is not None else 0
    stream_caps = sess.stream_caps if sess is not None else None
    return {
        "recording": alive,
        "frames_flowing": frames_flowing,
        "first_frame_latency_s": ff_latency,
        "frame_count": frame_count,
        "stream_caps": stream_caps,
        "base_stamp": base_stamp,
        "output_pattern": pattern,
        "phase": phase,
        "rotations": rotations,
        "pid": None,  # in-process pipeline; field kept for API back-compat
        "restarts": restarts,
        "last_exit": last_exit,
        "usb": usb_storage.get_status(),
    }
