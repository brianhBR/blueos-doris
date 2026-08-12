"""Tests for deferred post-dive processing and the cheap recovery quiesce."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from doris.services import dive_processing as module
from doris.services.dive_processing import (
    DiveProcessingService,
    StepSkipped,
    _copy_verified,
    _radcam_stamp_from_name,
    quiesce_dive,
)
from doris.services.dive_records import (
    set_mission_terminal_status,
    update_active_dive_record,
)


def _set_mtime(path: Path, when: datetime) -> None:
    import os

    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))


def _write_dive(dives_dir: Path, num: int, **fields) -> Path:
    dives_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "dive_name": f"dive {num}",
        "status": "active",
        "started_at": "2026-07-27T10:00:00+00:00",
    }
    record.update(fields)
    path = dives_dir / f"dive_{num:04d}.json"
    path.write_text(json.dumps(record))
    return path


# ── quiesce ───────────────────────────────────────────────────────────────


def test_quiesce_closes_the_dive_and_marks_it_pending(tmp_path, monkeypatch):
    """Recovery must leave a completed dive that is queued for processing."""
    dives = tmp_path / "dives"
    dive_file = _write_dive(dives, 7)
    (tmp_path / "mission_state.json").write_text(json.dumps({"status": "active"}))

    monkeypatch.setattr(module, "_data_root", lambda: tmp_path)
    recorder = _FakeRecorder(recording=True)
    monkeypatch.setitem(
        __import__("sys").modules, "doris.services.ip_camera_recorder", recorder
    )

    result = asyncio.run(quiesce_dive(stamp="20260727_100000", bottom_mode=2))

    assert result["success"] is True
    assert result["dive_file"] == "dive_0007.json"
    assert result["recorder_stopped"] is True
    assert recorder.stopped is True

    record = json.loads(dive_file.read_text())
    assert record["status"] == "completed"
    assert record["processing_state"] == "pending"
    assert record["dive_stamp"] == "20260727_100000"
    assert record["bottom_mode"] == 2
    assert record["ended_at"]

    mission = json.loads((tmp_path / "mission_state.json").read_text())
    assert mission["status"] == "completed"


def test_quiesce_does_no_heavy_work(tmp_path, monkeypatch):
    """The AGT waits on quiesce before cutting power, so ffmpeg must not run."""
    dives = tmp_path / "dives"
    _write_dive(dives, 1)
    monkeypatch.setattr(module, "_data_root", lambda: tmp_path)
    monkeypatch.setitem(
        __import__("sys").modules,
        "doris.services.ip_camera_recorder",
        _FakeRecorder(recording=False),
    )

    def _explode(*args, **kwargs):
        raise AssertionError("quiesce must not invoke post-processing")

    monkeypatch.setattr(module.binlog, "archive_dive_bin_logs", _explode)
    monkeypatch.setattr(module.dive_csv_export, "export_dive_csv_to_usb", _explode)

    result = asyncio.run(quiesce_dive())
    assert result["success"] is True


def test_quiesce_recovers_bottom_mode_from_dive_snapshot(tmp_path, monkeypatch):
    dives = tmp_path / "dives"
    dive_file = _write_dive(
        dives,
        2,
        configuration_snapshot={
            "bottom": {"camera": {"camera_type": "video-interval"}}
        },
    )
    monkeypatch.setattr(module, "_data_root", lambda: tmp_path)
    monkeypatch.setitem(
        __import__("sys").modules,
        "doris.services.ip_camera_recorder",
        _FakeRecorder(recording=False),
    )

    result = asyncio.run(quiesce_dive())

    assert result["bottom_mode"] == 2
    assert json.loads(dive_file.read_text())["bottom_mode"] == 2


def test_quiesce_is_idempotent(tmp_path, monkeypatch):
    """Repeated shutdown requests cannot rewrite a completed dive."""
    dives = tmp_path / "dives"
    dive_file = _write_dive(dives, 3)
    monkeypatch.setattr(module, "_data_root", lambda: tmp_path)
    monkeypatch.setitem(
        __import__("sys").modules,
        "doris.services.ip_camera_recorder",
        _FakeRecorder(recording=False),
    )

    first = asyncio.run(quiesce_dive(stamp="20260727_120000"))
    ended_at = json.loads(dive_file.read_text())["ended_at"]
    second = asyncio.run(quiesce_dive(stamp="20260727_120000"))

    assert first["dive_file"] == "dive_0003.json"
    # Nothing is left active, so the second call finds no dive to close and
    # must not rewrite the first call's end time.
    assert second["dive_file"] is None
    assert json.loads(dive_file.read_text())["ended_at"] == ended_at


def test_update_active_dive_record_marks_processing_pending(tmp_path):
    dives = tmp_path / "dives"
    dive_file = _write_dive(dives, 2)
    assert update_active_dive_record(dives, "cancelled") == dive_file
    record = json.loads(dive_file.read_text())
    assert record["status"] == "cancelled"
    # Even an aborted dive holds recoverable data worth processing.
    assert record["processing_state"] == "pending"


def test_terminal_mission_status_is_not_overwritten(tmp_path):
    path = tmp_path / "mission_state.json"
    path.write_text(json.dumps({"status": "completed", "completed_at": "then"}))
    set_mission_terminal_status(path, "cancelled")
    assert json.loads(path.read_text())["status"] == "completed"


def test_atomic_write_leaves_no_part_file(tmp_path):
    dives = tmp_path / "dives"
    dive_file = _write_dive(dives, 4)
    update_active_dive_record(dives, "completed")
    assert list(dives.glob("*.part")) == []
    assert json.loads(dive_file.read_text())["status"] == "completed"


# ── job runner ────────────────────────────────────────────────────────────


def test_only_one_job_runs_at_a_time(tmp_path, monkeypatch):
    dives = tmp_path / "dives"
    first = _write_dive(dives, 1, status="completed")
    second = _write_dive(dives, 2, status="completed")

    service = DiveProcessingService()
    started = asyncio.Event()

    async def _slow(session, ctx):
        started.set()
        await asyncio.sleep(0.2)
        return "ok"

    for key, _ in module._STEP_LABELS:
        monkeypatch.setattr(service, f"_step_{key}", _slow, raising=False)

    async def _scenario():
        session_id = service.start(first)
        await started.wait()
        with pytest.raises(RuntimeError, match="Already processing"):
            service.start(second)
        return session_id

    session_id = asyncio.run(_scenario())
    assert service.get_session(session_id) is not None


def test_failure_skips_remaining_steps_and_records_it(tmp_path, monkeypatch):
    dives = tmp_path / "dives"
    dive_file = _write_dive(dives, 5, status="completed")
    service = DiveProcessingService()

    async def _ok(session, ctx):
        return "fine"

    async def _boom(session, ctx):
        raise RuntimeError("USB fell out")

    for key, _ in module._STEP_LABELS:
        monkeypatch.setattr(service, f"_step_{key}", _ok, raising=False)
    monkeypatch.setattr(service, "_step_binlogs", _boom, raising=False)

    async def _scenario():
        session_id = service.start(dive_file)
        session = service.get_session(session_id)
        for _ in range(200):
            if session.done:
                break
            await asyncio.sleep(0.01)
        return session

    session = asyncio.run(_scenario())

    assert session.done is True
    assert session.success is False
    assert "USB fell out" in session.error
    assert session.step("binlogs").status == "failed"
    # Anything after the failure is skipped, not attempted.
    assert session.step("mcap").status == "skipped"
    assert session.step("flush").status == "skipped"
    # A failed run must never claim the stick is safe to pull.
    assert session.safe_to_remove_usb is False

    record = json.loads(dive_file.read_text())
    assert record["processing_state"] == "failed"
    assert "USB fell out" in record["processing_error"]


def test_skipped_steps_do_not_fail_the_job(tmp_path, monkeypatch):
    """Processing without a USB stick is still useful and must report success."""
    dives = tmp_path / "dives"
    dive_file = _write_dive(dives, 6, status="completed")
    service = DiveProcessingService()

    async def _ok(session, ctx):
        return "fine"

    async def _skip(session, ctx):
        raise StepSkipped("no USB stick")

    for key, _ in module._STEP_LABELS:
        monkeypatch.setattr(service, f"_step_{key}", _ok, raising=False)
    for key in ("binlogs", "mcap", "csv", "media", "record", "verify_usb", "flush"):
        monkeypatch.setattr(service, f"_step_{key}", _skip, raising=False)

    async def _scenario():
        session_id = service.start(dive_file)
        session = service.get_session(session_id)
        for _ in range(200):
            if session.done:
                break
            await asyncio.sleep(0.01)
        return session

    session = asyncio.run(_scenario())

    assert session.success is True
    assert session.step("video").status == "done"
    assert session.step("flush").status == "skipped"
    # The flush never ran, so the stick was never declared safe to remove.
    assert session.safe_to_remove_usb is False
    assert json.loads(dive_file.read_text())["processing_state"] == "complete"


def test_active_dive_is_refused(tmp_path, monkeypatch):
    dives = tmp_path / "dives"
    dive_file = _write_dive(dives, 8)  # still active
    service = DiveProcessingService()

    async def _scenario():
        session_id = service.start(dive_file)
        session = service.get_session(session_id)
        for _ in range(300):
            if session.done:
                break
            await asyncio.sleep(0.01)
        return session

    monkeypatch.setattr(module, "_data_root", lambda: tmp_path)
    session = asyncio.run(_scenario())

    assert session.success is False
    assert session.step("preflight").status == "failed"
    assert "still active" in session.step("preflight").detail


def test_verify_video_keeps_segments_when_output_is_bad(tmp_path):
    """The segments are the only copy, so a bad MP4 must not release them."""
    service = DiveProcessingService()
    segment = tmp_path / "radcam_20260727_100000_on_bottom_00001.ts"
    segment.write_bytes(b"raw video")
    empty_mp4 = tmp_path / "on_bottom.mp4"
    empty_mp4.touch()

    session = module.ProcessingSession(
        session_id="t", dive_id="dive_0001", dive_file=str(tmp_path),
        steps=[module.ProcessingStep(key=k, label=v) for k, v in module._STEP_LABELS],
    )
    ctx = {"video_outputs": [empty_mp4], "pending_deletions": [segment]}

    with pytest.raises(RuntimeError, match="empty"):
        asyncio.run(service._step_verify_video(session, ctx))
    assert segment.exists()


def test_verify_video_releases_segments_once_outputs_are_good(tmp_path):
    service = DiveProcessingService()
    segment = tmp_path / "radcam_20260727_100000_on_bottom_00001.ts"
    segment.write_bytes(b"raw video")
    good_mp4 = tmp_path / "on_bottom.mp4"
    good_mp4.write_bytes(b"encoded video")

    session = module.ProcessingSession(
        session_id="t", dive_id="dive_0001", dive_file=str(tmp_path),
        steps=[module.ProcessingStep(key=k, label=v) for k, v in module._STEP_LABELS],
    )
    ctx = {"video_outputs": [good_mp4], "pending_deletions": [segment]}

    detail = asyncio.run(service._step_verify_video(session, ctx))
    assert not segment.exists()
    assert "1 segment(s) released" in detail


# ── copy verification ─────────────────────────────────────────────────────


def test_copy_verified_matches_size_and_leaves_no_part_file(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 4096)
    dest = tmp_path / "out" / "src.bin"
    assert _copy_verified(src, dest) == 4096
    assert dest.read_bytes() == b"x" * 4096
    assert list(dest.parent.glob("*.part")) == []


def test_copy_verified_rejects_a_copy_that_did_not_land(tmp_path, monkeypatch):
    """A stick that silently discarded bytes must not be reported as good."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 100)
    dest = tmp_path / "out" / "src.bin"

    def _truncate_after_write(source, target):
        Path(target).write_bytes(b"x" * 40)

    monkeypatch.setattr(module.shutil, "copystat", _truncate_after_write)
    with pytest.raises(OSError, match="expected 100"):
        _copy_verified(src, dest)
    # A partial copy must never be left where it could look complete.
    assert not dest.exists()
    assert list(dest.parent.glob("*.part")) == []


def test_copy_verified_cleans_up_when_the_write_fails(tmp_path, monkeypatch):
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 100)
    dest = tmp_path / "out" / "src.bin"

    def _fail(fd):
        raise OSError("No space left on device")

    monkeypatch.setattr(module.os, "fsync", _fail)
    with pytest.raises(OSError, match="No space left"):
        _copy_verified(src, dest)
    assert not dest.exists()
    assert list(dest.parent.glob("*.part")) == []


def _processing_session() -> module.ProcessingSession:
    return module.ProcessingSession(
        session_id="t",
        dive_id="dive_0001",
        dive_file="x",
        steps=[module.ProcessingStep(key=k, label=v) for k, v in module._STEP_LABELS],
    )


def test_verify_usb_accepts_a_source_file_that_was_always_empty(tmp_path):
    """An empty extension log is not a failed copy, and must not sink the run."""
    service = DiveProcessingService()
    usb = tmp_path / "usb"
    usb.mkdir()
    empty = usb / "extension.log"
    empty.touch()

    ctx = {"usb_dir": usb, "copied": [(empty, 0)]}
    detail = asyncio.run(service._step_verify_usb(_processing_session(), ctx))

    assert "1 file(s)" in detail


def test_verify_usb_rejects_a_truncated_copy(tmp_path):
    service = DiveProcessingService()
    usb = tmp_path / "usb"
    usb.mkdir()
    short = usb / "telemetry.mcap"
    short.write_bytes(b"x" * 10)

    ctx = {"usb_dir": usb, "copied": [(short, 4096)]}
    with pytest.raises(OSError, match="truncated"):
        asyncio.run(service._step_verify_usb(_processing_session(), ctx))


def test_verify_usb_rejects_a_file_that_never_landed(tmp_path):
    service = DiveProcessingService()
    usb = tmp_path / "usb"
    usb.mkdir()

    ctx = {"usb_dir": usb, "copied": [(usb / "gone.bin", 12)]}
    with pytest.raises(OSError, match="missing"):
        asyncio.run(service._step_verify_usb(_processing_session(), ctx))


# ── RadCam Spy log selection ──────────────────────────────────────────────


def test_radcam_stamp_parsing():
    assert _radcam_stamp_from_name("radcam_20260727_101500.ndjson") == datetime(
        2026, 7, 27, 10, 15, 0, tzinfo=timezone.utc
    )
    assert _radcam_stamp_from_name("no-timestamp.ndjson") is None
    # A digit run somewhere else in the name is not a session start.
    assert _radcam_stamp_from_name("export_of_20260727_101500_notes.ndjson") is None


def test_radcam_collection_skips_empty_session_logs(tmp_path, monkeypatch):
    """An opened-but-unwritten session carries nothing worth copying."""
    logs_dir = tmp_path / "radcam" / "logs"
    logs_dir.mkdir(parents=True)
    written = logs_dir / "radcam_20260727_100500.ndjson"
    written.write_text('{"t":1}\n')
    empty = logs_dir / "radcam_20260727_101500.ndjson"
    empty.touch()
    for f in (written, empty):
        _set_mtime(f, datetime(2026, 7, 27, 10, 30, tzinfo=timezone.utc))

    monkeypatch.setattr(module.settings, "radcam_spy_logs_dir", str(logs_dir))
    service = DiveProcessingService()
    start = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    dest = tmp_path / "out"

    names = asyncio.run(
        service._collect_radcam_from_mount(start, start + timedelta(hours=1), dest)
    )

    assert names == [written.name]
    assert not (dest / empty.name).exists()


def test_radcam_collection_picks_only_overlapping_sessions(tmp_path, monkeypatch):
    logs_dir = tmp_path / "radcam" / "logs"
    logs_dir.mkdir(parents=True)
    during = logs_dir / "radcam_20260727_100500.ndjson"
    before = logs_dir / "radcam_20260720_080000.ndjson"
    for f in (during, before):
        f.write_text('{"t":1}\n')
    # A session spans its filename stamp to its last write, so the mtime is
    # what decides overlap for a session that started before the dive.
    _set_mtime(during, datetime(2026, 7, 27, 10, 30, tzinfo=timezone.utc))
    _set_mtime(before, datetime(2026, 7, 20, 8, 30, tzinfo=timezone.utc))

    monkeypatch.setattr(module.settings, "radcam_spy_logs_dir", str(logs_dir))
    service = DiveProcessingService()
    start = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    dest = tmp_path / "out"

    names = asyncio.run(service._collect_radcam_from_mount(start, end, dest))

    assert names == [during.name]
    assert (dest / during.name).exists()
    assert not (dest / before.name).exists()


def test_radcam_collection_returns_none_without_the_mount(tmp_path, monkeypatch):
    """Signals the caller to fall back to the extension's HTTP API."""
    monkeypatch.setattr(
        module.settings, "radcam_spy_logs_dir", str(tmp_path / "absent")
    )
    service = DiveProcessingService()
    start = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    result = asyncio.run(
        service._collect_radcam_from_mount(start, start, tmp_path / "out")
    )
    assert result is None


class _FakeRecorder:
    """Stand-in for services.ip_camera_recorder during quiesce."""

    SNAPSHOT_SUBDIR = "photos"

    def __init__(self, recording: bool) -> None:
        self._recording = recording
        self.stopped = False

    def is_recording(self) -> bool:
        return self._recording

    async def stop_recording(self) -> None:
        self._recording = False
        self.stopped = True

    def last_base_stamp(self) -> str | None:
        return None

    def clear_snapshot_state(self) -> None:
        return None
