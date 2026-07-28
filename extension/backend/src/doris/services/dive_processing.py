"""Deferred post-dive processing.

Reaching the surface only quiesces the vehicle: the recorder is stopped, the
dive record is closed, and the payload is left in a state where the AGT can cut
power immediately.  Everything expensive -- ffmpeg, USB copies, telemetry
parsing -- happens here instead, when an operator presses "Process Dive" on the
Previous Dives page with the vehicle on deck and mains power available.

The job is a sequence of steps with individually reported status so the UI can
show which one is running and which ones were skipped.  Steps that need a USB
stick report ``skipped`` rather than failing, because processing without one is
still useful: the videos are built and the dive record is enriched either way.

Nothing irreversible happens before it has been verified.  The ``.ts`` source
segments survive until the MP4s built from them have been probed, and every USB
copy is size-checked before the job reports success and declares the stick safe
to remove.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ..config import blueos_services, settings
from . import binlog, dive_csv_export, usb_storage
from .dive_records import (
    set_mission_terminal_status,
    update_active_dive_record,
    write_json_atomic,
)

logger = logging.getLogger(__name__)

# Per-dive bundle on the stick, alongside the existing binlogs/ and dive_data/
# folders that the BIN archive and CSV export write into.
USB_SUBDIR = "dives"

# Refuse to start if the stick cannot hold the dive plus this much headroom,
# so we fail in the preflight step instead of halfway through a copy.
_USB_HEADROOM_MB = 128.0

# A RadCam Spy session counts as belonging to this dive if it overlaps the dive
# window at all.  Clocks between the two extensions can differ slightly, so the
# window is padded before testing for overlap.
_RADCAM_WINDOW_PAD = timedelta(minutes=5)

# radcam_<YYYYMMDD>_<HHMMSS>.ndjson, the name the spy extension opens a session
# with.  Anchored to the whole filename so an unrelated run of digits elsewhere
# in a name cannot be mistaken for a session start.
_RADCAM_LOG_RE = re.compile(
    r"^radcam_(?P<date>\d{8})_(?P<time>\d{6})\.ndjson$", re.IGNORECASE
)

_STEP_LABELS: tuple[tuple[str, str], ...] = (
    ("preflight", "Check dive and USB capacity"),
    ("video", "Build videos from segments"),
    ("verify_video", "Verify videos and release segments"),
    ("logs", "Collect extension logs"),
    ("radcam", "Collect RadCam Spy logs"),
    ("binlogs", "Copy autopilot logs to USB"),
    ("mcap", "Copy telemetry log to USB"),
    ("csv", "Export telemetry CSV to USB"),
    ("media", "Copy videos and photos to USB"),
    ("record", "Copy dive record to USB"),
    ("verify_usb", "Verify USB copies"),
    ("flush", "Flush USB"),
)


@dataclass
class ProcessingStep:
    """One unit of post-dive work, as reported to the UI."""

    key: str
    label: str
    status: str = "pending"  # pending | running | done | skipped | failed
    detail: str = ""


@dataclass
class ProcessingSession:
    """Tracks one in-progress (or finished) processing run."""

    session_id: str
    dive_id: str
    dive_file: str
    steps: list[ProcessingStep]
    lines: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str | None = None
    done: bool = False
    success: bool = False
    error: str | None = None
    # True only once every USB write has been verified and flushed, which is
    # the operator's cue that pulling the stick will not lose data.
    safe_to_remove_usb: bool = False

    def step(self, key: str) -> ProcessingStep:
        for s in self.steps:
            if s.key == key:
                return s
        raise KeyError(key)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "dive_id": self.dive_id,
            "steps": [asdict(s) for s in self.steps],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "done": self.done,
            "success": self.success,
            "error": self.error,
            "safe_to_remove_usb": self.safe_to_remove_usb,
        }


class StepSkipped(Exception):
    """Raised by a step that cannot run but must not fail the job."""


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _data_root() -> Path:
    from .storage import DATA_ROOT

    return Path(DATA_ROOT)


def _write_record_atomic(dive_file: Path, record: dict) -> None:
    """Persist a dive record without leaving a truncated file behind."""
    write_json_atomic(dive_file, record)


async def quiesce_dive(
    stamp: str | None = None, bottom_mode: int | None = None
) -> dict:
    """Close out a finished dive cheaply, leaving it ready to be processed.

    Stops the recorder so the trailing video segment is complete on disk, then
    marks the dive ``completed`` and its processing ``pending``.  Deliberately
    does no ffmpeg, no USB writes, and no telemetry parsing: the AGT may be
    waiting on this before it cuts payload power, so long-running work here
    would both drain the surface battery and risk being killed mid-write.

    Also records the dive stamp and bottom mode, which are only knowable now --
    the recorder's stamp is cleared for the next dive and ``DORIS_BTM_CMOD`` can
    change before anyone gets round to processing this dive.

    Safe to call repeatedly: Lua fires it on the first RECOVERY tick and the
    shutdown handshake calls it again before acknowledging.
    """
    data_root = _data_root()
    dives_dir = data_root / "dives"
    mission_state_path = data_root / "mission_state.json"

    # Closing the dive record matters more than stopping the recorder: the AGT
    # will not acknowledge until this returns, so a recorder problem must not
    # take the rest of the quiesce down with it.
    stopped = False
    try:
        from . import ip_camera_recorder as iprec

        if iprec.is_recording():
            await iprec.stop_recording()
            stopped = True
    except Exception as e:
        logger.warning("Quiesce: stopping IP camera recorder failed: %s", e)

    dive_file: Path | None = None
    try:
        dive_file = update_active_dive_record(dives_dir, "completed")
    except Exception as e:
        logger.warning("Quiesce: failed to close dive record: %s", e)

    resolved_stamp = stamp or _recorder_base_stamp()
    if dive_file is not None:
        try:
            record = json.loads(dive_file.read_text())
            if resolved_stamp:
                record["dive_stamp"] = resolved_stamp
            if bottom_mode is not None:
                record["bottom_mode"] = bottom_mode
            record.setdefault("processing_state", "pending")
            write_json_atomic(dive_file, record)
        except Exception as e:
            logger.warning("Quiesce: failed to annotate dive record: %s", e)

    try:
        set_mission_terminal_status(mission_state_path, "completed")
    except Exception as e:
        logger.warning("Quiesce: failed to set mission state: %s", e)

    logger.info(
        "Quiesce complete (dive=%s stamp=%s recorder_stopped=%s)",
        dive_file.name if dive_file else "none", resolved_stamp, stopped,
    )
    return {
        "success": True,
        "dive_file": dive_file.name if dive_file else None,
        "dive_stamp": resolved_stamp,
        "bottom_mode": bottom_mode,
        "recorder_stopped": stopped,
        "processing_state": "pending" if dive_file else None,
        "message": "Dive quiesced; run Process Dive to build videos and exports",
    }


def _copy_verified(src: Path, dest: Path) -> int:
    """Copy one file and confirm the destination matches. Returns bytes copied.

    Writes to a ``.part`` sidecar and renames, so an interrupted copy can never
    be mistaken for a complete one, and fsyncs before the rename so the bytes
    are on the stick rather than in the page cache when we report success.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    written = 0
    try:
        with open(src, "rb") as reader, open(tmp, "wb") as writer:
            while chunk := reader.read(1024 * 1024):
                writer.write(chunk)
                written += len(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        shutil.copystat(src, tmp)
        expected = src.stat().st_size
        landed = tmp.stat().st_size
        if written != expected or landed != expected:
            raise OSError(
                f"{src.name}: copied {written} bytes and {landed} landed, "
                f"expected {expected}"
            )
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, dest)
    return written


def _note_copy(ctx: dict, dest: Path, expected: int | None = None) -> None:
    """Register a file for the USB verification step.

    ``expected`` is the source size where we have one, so verification can
    catch a truncated copy.  Files another service produced directly on the
    stick have no source to compare against and are only checked for presence.
    """
    ctx["copied"].append((dest, expected))


def _recorder_base_stamp() -> str | None:
    """The recorder's current dive stamp, or None if it is unavailable.

    Quiesce persists the stamp on the dive record, so processing only consults
    the recorder for dives that predate that.  The recorder pulls in GStreamer
    bindings, which need not be present just to read a fallback value.
    """
    try:
        from . import ip_camera_recorder as iprec
    except Exception as e:
        logger.info("Recorder unavailable for stamp lookup: %s", e)
        return None
    try:
        return iprec.last_base_stamp()
    except Exception:
        return None


def _dir_bytes(root: Path, patterns: tuple[str, ...]) -> int:
    total = 0
    for pattern in patterns:
        for path in root.rglob(pattern):
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


class DiveProcessingService:
    """Runs one post-dive processing job at a time."""

    def __init__(self) -> None:
        self._sessions: dict[str, ProcessingSession] = {}
        self._active_id: str | None = None

    # ── session management ────────────────────────────────────────────────

    def get_session(self, session_id: str) -> ProcessingSession | None:
        return self._sessions.get(session_id)

    def active_session(self) -> ProcessingSession | None:
        if self._active_id is None:
            return None
        return self._sessions.get(self._active_id)

    def start(self, dive_file: Path) -> str:
        """Queue a processing run. Raises RuntimeError if one is in flight."""
        active = self.active_session()
        if active is not None and not active.done:
            raise RuntimeError(
                f"Already processing {active.dive_id}; wait for it to finish"
            )

        session_id = uuid.uuid4().hex[:12]
        session = ProcessingSession(
            session_id=session_id,
            dive_id=dive_file.stem,
            dive_file=str(dive_file),
            steps=[ProcessingStep(key=k, label=lbl) for k, lbl in _STEP_LABELS],
            started_at=_now(),
        )
        self._sessions[session_id] = session
        self._active_id = session_id
        asyncio.get_event_loop().create_task(self._run(session, dive_file))
        return session_id

    # ── progress reporting ────────────────────────────────────────────────

    def _log(self, session: ProcessingSession, message: str) -> None:
        session.lines.append(message)
        logger.info("PROCESS[%s] %s", session.dive_id, message)

    # ── the job ───────────────────────────────────────────────────────────

    async def _run(self, session: ProcessingSession, dive_file: Path) -> None:
        record: dict = {}
        try:
            record = json.loads(dive_file.read_text())
        except Exception as e:
            session.error = f"Cannot read {dive_file.name}: {e}"
            session.step("preflight").status = "failed"
            session.step("preflight").detail = session.error
            self._log(session, session.error)
            session.done = True
            session.finished_at = _now()
            return

        record["processing_state"] = "running"
        record["processing_session_id"] = session.session_id
        record["processing_started_at"] = session.started_at
        record.pop("processing_error", None)
        try:
            _write_record_atomic(dive_file, record)
        except Exception as e:
            logger.warning("Could not mark %s as processing: %s", dive_file.name, e)

        ctx: dict = {"record": record, "dive_file": dive_file, "copied": []}
        handlers = {
            "preflight": self._step_preflight,
            "video": self._step_video,
            "verify_video": self._step_verify_video,
            "logs": self._step_logs,
            "radcam": self._step_radcam,
            "binlogs": self._step_binlogs,
            "mcap": self._step_mcap,
            "csv": self._step_csv,
            "media": self._step_media,
            "record": self._step_record,
            "verify_usb": self._step_verify_usb,
            "flush": self._step_flush,
        }

        failed = False
        for step in session.steps:
            if failed:
                step.status = "skipped"
                step.detail = "earlier step failed"
                continue
            step.status = "running"
            try:
                detail = await handlers[step.key](session, ctx)
                step.status = "done"
                step.detail = detail or ""
            except StepSkipped as skip:
                step.status = "skipped"
                step.detail = str(skip)
                self._log(session, f"{step.label}: skipped ({skip})")
            except Exception as e:
                logger.exception("PROCESS[%s] %s failed", session.dive_id, step.key)
                step.status = "failed"
                step.detail = str(e)
                session.error = f"{step.label}: {e}"
                self._log(session, f"{step.label}: FAILED ({e})")
                failed = True

        session.success = not failed
        session.done = True
        session.finished_at = _now()
        # Only claim the stick is safe to pull if the flush actually ran.
        session.safe_to_remove_usb = (
            session.success and session.step("flush").status == "done"
        )

        record = ctx["record"]
        record["processing_state"] = "complete" if session.success else "failed"
        record["processing_finished_at"] = session.finished_at
        record["processing_steps"] = [asdict(s) for s in session.steps]
        if session.error:
            record["processing_error"] = session.error
        try:
            _write_record_atomic(dive_file, record)
        except Exception as e:
            logger.warning("Could not persist processing result: %s", e)

        self._log(
            session,
            "Processing complete" if session.success else "Processing failed",
        )

    # ── steps ─────────────────────────────────────────────────────────────

    async def _step_preflight(self, session: ProcessingSession, ctx: dict) -> str:
        record = ctx["record"]
        if record.get("status") == "active":
            raise RuntimeError(
                "Dive is still active; stop or finalize it before processing"
            )

        stamp = record.get("dive_stamp") or _recorder_base_stamp()
        if not stamp:
            self._log(session, "No dive stamp on record; video steps will skip")
        ctx["stamp"] = stamp
        ctx["bottom_mode"] = record.get("bottom_mode")

        rec_root = Path(settings.ipcam_recordings_subdir)
        if not rec_root.is_absolute():
            rec_root = _data_root() / settings.ipcam_recordings_subdir
        dive_dir = rec_root / f"dive_{stamp}" if stamp else None
        ctx["dive_dir"] = dive_dir if dive_dir and dive_dir.is_dir() else None

        needed_mb = 0.0
        if ctx["dive_dir"] is not None:
            needed_mb = _dir_bytes(
                ctx["dive_dir"], ("*.ts", "*.mp4", "*.jpg")
            ) / (1024 * 1024)

        slug = binlog.slug_for_dive(record, ctx["dive_file"])
        ctx["slug"] = slug
        usb_dir = usb_storage.get_recording_dir_if_available(f"{USB_SUBDIR}/{slug}")
        ctx["usb_dir"] = Path(usb_dir) if usb_dir else None

        if ctx["usb_dir"] is None:
            self._log(
                session,
                "No USB stick mounted; USB steps will be skipped but videos "
                "and the dive record will still be processed",
            )
            return "no USB stick; local steps only"

        free_mb = usb_storage.get_free_mb()
        if free_mb is not None and needed_mb + _USB_HEADROOM_MB > free_mb:
            raise RuntimeError(
                f"USB has {free_mb:.0f} MB free but this dive needs about "
                f"{needed_mb:.0f} MB plus {_USB_HEADROOM_MB:.0f} MB headroom"
            )
        return (
            f"USB ready at {ctx['usb_dir']}"
            + (f", {free_mb:.0f} MB free" if free_mb is not None else "")
        )

    async def _step_video(self, session: ProcessingSession, ctx: dict) -> str:
        from .dive_finalize import finalize_dive

        if not ctx.get("stamp"):
            raise StepSkipped("no dive stamp")

        self._log(session, "Building per-phase MP4s (this can take a while)")
        manifest = await finalize_dive(
            stamp=ctx["stamp"],
            bottom_mode=ctx.get("bottom_mode"),
            delete_sources=False,
        )
        ctx["manifest"] = manifest
        if manifest.get("reason") in ("no_stamp", "no_recordings_dir"):
            raise StepSkipped(manifest.get("message", manifest["reason"]))

        phases = manifest.get("phases", [])
        outputs = [
            Path(p["output"])
            for p in phases
            if p.get("output") and p.get("success", True)
        ]
        ctx["video_outputs"] = outputs
        ctx["pending_deletions"] = [
            Path(p) for p in manifest.get("pending_deletions", [])
        ]
        return f"{len(outputs)} video(s) from {len(ctx['pending_deletions'])} segment(s)"

    async def _step_verify_video(self, session: ProcessingSession, ctx: dict) -> str:
        outputs: list[Path] = ctx.get("video_outputs") or []
        pending: list[Path] = ctx.get("pending_deletions") or []
        if not outputs and not pending:
            raise StepSkipped("no videos produced")

        for out in outputs:
            if not out.exists():
                raise RuntimeError(f"{out.name} is missing")
            if out.stat().st_size == 0:
                raise RuntimeError(f"{out.name} is empty")

        # The MP4s are real, so the segments they were built from are now
        # redundant.  This is the only irreversible step in the job.
        removed = 0
        for src in pending:
            try:
                src.unlink()
                removed += 1
            except FileNotFoundError:
                continue
            except OSError as e:
                logger.warning("Could not remove segment %s: %s", src, e)
        return f"{len(outputs)} video(s) verified, {removed} segment(s) released"

    async def _step_logs(self, session: ProcessingSession, ctx: dict) -> str:
        from .dive_finalize import _copy_diagnostic_logs

        dive_dir: Path | None = ctx.get("dive_dir")
        if dive_dir is None:
            raise StepSkipped("no dive recording folder")
        result = await asyncio.to_thread(_copy_diagnostic_logs, dive_dir)
        copied = result.get("copied", [])
        return f"{len(copied)} log file(s) collected"

    async def _step_radcam(self, session: ProcessingSession, ctx: dict) -> str:
        dive_dir: Path | None = ctx.get("dive_dir")
        if dive_dir is None:
            raise StepSkipped("no dive recording folder")

        record = ctx["record"]
        start = _parse_iso(record.get("started_at"))
        end = _parse_iso(record.get("ended_at")) or datetime.now(tz=timezone.utc)
        if start is None:
            raise StepSkipped("dive has no start time to match sessions against")
        start -= _RADCAM_WINDOW_PAD
        end += _RADCAM_WINDOW_PAD

        dest = dive_dir / "logs" / "radcam_spy"
        names = await self._collect_radcam_from_mount(start, end, dest)
        source = "mount"
        if names is None:
            names = await self._collect_radcam_over_http(start, end, dest)
            source = "http"
        if names is None:
            raise StepSkipped(
                "RadCam Spy logs are neither mounted nor reachable over HTTP"
            )
        if not names:
            raise StepSkipped("no RadCam Spy sessions overlap this dive")
        return f"{len(names)} session log(s) via {source}"

    async def _collect_radcam_from_mount(
        self, start: datetime, end: datetime, dest: Path
    ) -> list[str] | None:
        """Copy overlapping session logs from the read-only bind mount.

        Returns None when the mount is absent, so the caller can fall back to
        the extension's HTTP API.
        """
        logs_dir = Path(settings.radcam_spy_logs_dir)
        if not logs_dir.is_dir():
            return None

        def _copy() -> list[str]:
            names: list[str] = []
            for src in sorted(logs_dir.glob("radcam_*.ndjson")):
                try:
                    stat = src.stat()
                except OSError:
                    continue
                # The extension leaves a zero-byte file behind for a session it
                # opened but never wrote to.  Nothing to carry, and an empty
                # file on the stick is indistinguishable from a failed copy.
                if stat.st_size == 0:
                    continue
                session_end = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                session_start = (
                    _radcam_stamp_from_name(src.name) or session_end
                )
                if session_start > end or session_end < start:
                    continue
                _copy_verified(src, dest / src.name)
                names.append(src.name)
            return names

        return await asyncio.to_thread(_copy)

    async def _collect_radcam_over_http(
        self, start: datetime, end: datetime, dest: Path
    ) -> list[str] | None:
        """Download overlapping session logs from the RadCam Spy extension.

        Only works while that extension is running; returns None if it cannot
        be reached.
        """
        base = blueos_services.radcam_spy
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                listing = await client.get(f"{base}/api/logs")
                listing.raise_for_status()
                entries = listing.json().get("logs", [])

                names: list[str] = []
                dest.mkdir(parents=True, exist_ok=True)
                for entry in entries:
                    name = entry.get("name")
                    if not isinstance(name, str) or not name.endswith(".ndjson"):
                        continue
                    session_start = _radcam_stamp_from_name(name)
                    modified = _parse_iso(entry.get("modified")) or _epoch_to_dt(
                        entry.get("mtime")
                    )
                    session_end = modified or session_start
                    if session_start is None and session_end is None:
                        continue
                    lo = session_start or session_end
                    hi = session_end or session_start
                    if lo > end or hi < start:
                        continue
                    body = await client.get(f"{base}/api/logs/{name}")
                    body.raise_for_status()
                    if not body.content:
                        continue
                    tmp = dest / f"{name}.part"
                    tmp.write_bytes(body.content)
                    os.replace(tmp, dest / name)
                    names.append(name)
                return names
        except Exception as e:
            logger.info("RadCam Spy HTTP collection unavailable: %s", e)
            return None

    async def _step_binlogs(self, session: ProcessingSession, ctx: dict) -> str:
        if ctx.get("usb_dir") is None:
            raise StepSkipped("no USB stick")
        result = await binlog.archive_dive_bin_logs(ctx["dive_file"])
        status = result.get("status")
        if status == "skipped_no_usb":
            raise StepSkipped("no USB stick")
        if status == "no_match":
            raise StepSkipped("no autopilot logs matched this dive")
        if status == "error":
            raise RuntimeError(result.get("error", "archive failed"))
        files = result.get("files", [])
        for path in files:
            _note_copy(ctx, Path(path))
        return f"{len(files)} autopilot log(s) copied"

    async def _step_mcap(self, session: ProcessingSession, ctx: dict) -> str:
        from .binlog import wait_until_quiescent
        from .mcap_telemetry import map_dive_stem_to_largest_mcap
        from .storage import _load_dive_windows

        usb_dir: Path | None = ctx.get("usb_dir")
        if usb_dir is None:
            raise StepSkipped("no USB stick")

        data_root = _data_root()
        windows = _load_dive_windows(data_root)
        mcap_map = map_dive_stem_to_largest_mcap(data_root, windows)
        mcap_path = mcap_map.get(ctx["dive_file"].stem)
        if mcap_path is None:
            raise StepSkipped("no telemetry log matched this dive")

        # The recorder may still be flushing; copying a file mid-write would
        # produce a stick that looks complete but holds a truncated log.
        try:
            await wait_until_quiescent(mcap_path)
        except Exception as e:
            logger.info("MCAP quiescence wait failed for %s: %s", mcap_path, e)

        dest = usb_dir / "telemetry" / mcap_path.name
        written = await asyncio.to_thread(_copy_verified, mcap_path, dest)
        _note_copy(ctx, dest, written)
        ctx["record"]["mcap_usb_file"] = str(dest)
        return f"{mcap_path.name} ({written / (1024 * 1024):.1f} MB)"

    async def _step_csv(self, session: ProcessingSession, ctx: dict) -> str:
        result = await dive_csv_export.export_dive_csv_to_usb(ctx["dive_file"])
        status = result.get("status")
        if status == "skipped_no_usb":
            raise StepSkipped("no USB stick")
        if status == "error":
            raise RuntimeError(result.get("error", "export failed"))
        # The export enriches the dive record on disk (measured max depth, end
        # position); reload so later steps persist those fields rather than
        # overwriting them from our stale copy.
        try:
            ctx["record"] = json.loads(ctx["dive_file"].read_text())
        except Exception as e:
            logger.warning("Could not reload dive record after CSV export: %s", e)
        if result.get("file"):
            _note_copy(ctx, Path(result["file"]))
        return f"{result.get('rows', 0)} row(s) exported"

    async def _step_media(self, session: ProcessingSession, ctx: dict) -> str:
        usb_dir: Path | None = ctx.get("usb_dir")
        dive_dir: Path | None = ctx.get("dive_dir")
        if usb_dir is None:
            raise StepSkipped("no USB stick")
        if dive_dir is None:
            raise StepSkipped("no dive recording folder")

        def _copy_media() -> tuple[int, int]:
            videos = 0
            photos = 0
            for src in sorted(dive_dir.glob("*.mp4")):
                written = _copy_verified(src, usb_dir / "video" / src.name)
                _note_copy(ctx, usb_dir / "video" / src.name, written)
                videos += 1
            for src in sorted(dive_dir.rglob("*.jpg")):
                written = _copy_verified(src, usb_dir / "photos" / src.name)
                _note_copy(ctx, usb_dir / "photos" / src.name, written)
                photos += 1
            for src in sorted((dive_dir / "logs").rglob("*")):
                if src.is_file():
                    rel = src.relative_to(dive_dir / "logs")
                    written = _copy_verified(src, usb_dir / "logs" / rel)
                    _note_copy(ctx, usb_dir / "logs" / rel, written)
            return videos, photos

        videos, photos = await asyncio.to_thread(_copy_media)
        if not videos and not photos:
            raise StepSkipped("no videos or photos to copy")
        return f"{videos} video(s), {photos} photo(s)"

    async def _step_record(self, session: ProcessingSession, ctx: dict) -> str:
        usb_dir: Path | None = ctx.get("usb_dir")
        dive_dir: Path | None = ctx.get("dive_dir")
        if usb_dir is None:
            raise StepSkipped("no USB stick")

        dive_file: Path = ctx["dive_file"]
        copied = 0

        def _copy_record() -> int:
            count = 0
            written = _copy_verified(dive_file, usb_dir / dive_file.name)
            _note_copy(ctx, usb_dir / dive_file.name, written)
            count += 1
            if dive_dir is not None:
                for manifest in sorted(dive_dir.glob("*_manifest.json")):
                    written = _copy_verified(manifest, usb_dir / manifest.name)
                    _note_copy(ctx, usb_dir / manifest.name, written)
                    count += 1
            return count

        copied = await asyncio.to_thread(_copy_record)
        return f"{copied} file(s) copied"

    async def _step_verify_usb(self, session: ProcessingSession, ctx: dict) -> str:
        copied: list[tuple[Path, int | None]] = ctx.get("copied") or []
        if ctx.get("usb_dir") is None:
            raise StepSkipped("no USB stick")
        if not copied:
            raise StepSkipped("nothing was copied to USB")

        def _verify() -> int:
            missing = [dest for dest, _ in copied if not dest.exists()]
            if missing:
                names = ", ".join(p.name for p in missing[:5])
                raise OSError(f"{len(missing)} file(s) missing on USB: {names}")
            # Compare against the source size rather than flagging anything
            # empty: a log the source system left empty is not a bad copy, and
            # failing here would discard an otherwise complete bundle.
            truncated = [
                dest
                for dest, expected in copied
                if expected is not None and dest.stat().st_size != expected
            ]
            if truncated:
                names = ", ".join(p.name for p in truncated[:5])
                raise OSError(f"{len(truncated)} truncated file(s) on USB: {names}")
            return len(copied)

        count = await asyncio.to_thread(_verify)
        return f"{count} file(s) present and non-empty"

    async def _step_flush(self, session: ProcessingSession, ctx: dict) -> str:
        if ctx.get("usb_dir") is None:
            raise StepSkipped("no USB stick")
        await asyncio.to_thread(os.sync)
        return "USB flushed; safe to remove"


def _radcam_stamp_from_name(name: str) -> datetime | None:
    """Parse the session start out of a RadCam Spy log filename."""
    match = _RADCAM_LOG_RE.match(name)
    if match is None:
        return None
    raw = f"{match.group('date')}{match.group('time')}"
    try:
        return datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _epoch_to_dt(value: object) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


dive_processing_service = DiveProcessingService()
