"""ArduPilot BIN log archiver.

After a dive ends (completed or cancelled), copy the matching ArduPilot
``.BIN`` log(s) from the firmware logs directory to the external USB
drive under a dive-named filename, so they show up in the DORIS Data tab.

ArduPilot writes a new ``00000NNN.BIN`` file every arm/disarm cycle and
keeps the latest log number in ``LASTLOG.TXT``.  The Dockerfile bind-mounts
the host's ``/root/.config/blueos/ardupilot-manager/firmware`` directory
into the container at ``/tmp/storage/firmware``, so the BIN files are
visible at :data:`BINLOG_DIR` ``/tmp/storage/firmware/logs``.

Selection rules (see :func:`select_bin_logs_for_dive`):

1. Primary: every BIN whose log number is in
   ``[bin_log_start_num + 1, current_LASTLOG]`` (range captured at dive
   start).  ``bin_log_start_num + 1`` because the LASTLOG read at dive
   start is the *previous* completed log; the dive's own arm cycle bumps
   the counter by at least one.
2. Fallback (no ``bin_log_start_num`` recorded, e.g. a legacy dive or
   the LASTLOG file was unreadable): pick BIN files whose *active write
   interval* overlaps the dive window.  A BIN file ``N`` has been
   actively written from the previous BIN's mtime (``N-1``) up to its
   own mtime (last write before disarm), so we treat it as active
   during ``(prev_mtime, mtime]``.  A BIN matches when this interval
   overlaps ``[started_at - 30s, ended_at + 60s]``.
3. Skip empty / zero-byte files in either case.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import usb_storage

logger = logging.getLogger(__name__)


BINLOG_DIR = Path(os.environ.get("DORIS_BINLOG_DIR", "/tmp/storage/firmware/logs"))
LASTLOG_NAME = "LASTLOG.TXT"
USB_SUBDIR = "binlogs"

_BIN_RE = re.compile(r"^(\d+)\.BIN$", re.IGNORECASE)

_FALLBACK_PRE_S = 30
_FALLBACK_POST_S = 60


# ── LASTLOG / BIN discovery ─────────────────────────────────────────


def read_lastlog_num(logs_dir: Path | None = None) -> int | None:
    """Return the integer in ``LASTLOG.TXT`` or ``None`` if unavailable.

    ArduPilot increments this counter every arm cycle, so reading it at
    dive start gives a stable lower bound for the dive's BIN logs.
    """
    base = logs_dir or BINLOG_DIR
    p = base / LASTLOG_NAME
    try:
        text = p.read_text(errors="replace").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def iter_bin_logs(
    min_num: int | None = None,
    max_num: int | None = None,
    *,
    logs_dir: Path | None = None,
) -> Iterator[tuple[int, Path]]:
    """Yield ``(log_num, path)`` for every numbered ``.BIN`` in the logs dir.

    Filters by inclusive ``min_num`` / ``max_num`` when provided.  Skips
    entries with non-numeric names (e.g. ``LASTLOG.TXT``).
    """
    base = logs_dir or BINLOG_DIR
    try:
        entries = list(base.iterdir())
    except OSError:
        return
    for path in entries:
        if not path.is_file():
            continue
        m = _BIN_RE.match(path.name)
        if not m:
            continue
        try:
            num = int(m.group(1))
        except ValueError:
            continue
        if min_num is not None and num < min_num:
            continue
        if max_num is not None and num > max_num:
            continue
        yield num, path


# ── selection ───────────────────────────────────────────────────────


def _parse_iso_to_utc(value: object) -> datetime | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bin_log_start_num(record: dict) -> int | None:
    raw = record.get("bin_log_start_num")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def select_bin_logs_for_dive(
    dive_record: dict,
    *,
    logs_dir: Path | None = None,
) -> list[Path]:
    """Return the ``.BIN`` files that belong to this dive, newest-first.

    See module docstring for rules.  Always sorted by log number ascending.
    Empty / unreadable files are skipped.
    """
    base = logs_dir or BINLOG_DIR

    def _nonempty(path: Path) -> bool:
        try:
            return path.stat().st_size > 0
        except OSError:
            return False

    start_num = _bin_log_start_num(dive_record)
    current = read_lastlog_num(base)

    primary: list[tuple[int, Path]] = []
    if start_num is not None and current is not None and current > start_num:
        primary = [
            (n, p)
            for n, p in iter_bin_logs(
                min_num=start_num + 1, max_num=current, logs_dir=base
            )
            if _nonempty(p)
        ]
    if primary:
        primary.sort(key=lambda x: x[0])
        return [p for _, p in primary]

    # Fallback: pick BINs whose *active write interval* overlaps the dive window.
    # A BIN's interval is (prev_BIN.mtime, this.mtime].  We can't see when a
    # BIN started being written, so we approximate it by the previous BIN's
    # mtime (the moment the previous arm cycle ended).  Without this, fast
    # dives whose BIN finalizes well after the dive ended would be skipped.
    started = _parse_iso_to_utc(dive_record.get("started_at"))
    ended = _parse_iso_to_utc(dive_record.get("ended_at"))
    if started is None:
        return []
    if ended is None or ended < started:
        ended = datetime.now(tz=timezone.utc)

    lo = started - timedelta(seconds=_FALLBACK_PRE_S)
    hi = ended + timedelta(seconds=_FALLBACK_POST_S)

    bins_with_mtime: list[tuple[int, Path, datetime]] = []
    for num, path in iter_bin_logs(logs_dir=base):
        if not _nonempty(path):
            continue
        try:
            m = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        bins_with_mtime.append((num, path, m))
    bins_with_mtime.sort(key=lambda x: x[0])

    # Build prev_mtime per BIN (by ascending num).  The "active" interval is
    # (prev_mtime, mtime].  If there is no predecessor, treat prev_mtime as
    # the epoch so the predicate becomes simply ``mtime > lo``.
    epoch = datetime.fromtimestamp(0, tz=timezone.utc)
    matches: list[tuple[int, Path]] = []
    for idx, (num, path, mtime) in enumerate(bins_with_mtime):
        prev_mtime = bins_with_mtime[idx - 1][2] if idx > 0 else epoch
        # Active interval (prev_mtime, mtime] overlaps [lo, hi] iff
        #   prev_mtime < hi  AND  mtime > lo  (using > to honour the open
        #   lower bound -- a BIN whose mtime equals the dive start is the
        #   PREVIOUS one).
        if prev_mtime < hi and mtime > lo:
            matches.append((num, path))
    return [p for _, p in matches]


# ── quiescence ──────────────────────────────────────────────────────


async def wait_until_quiescent(
    path: Path,
    *,
    idle_s: float = 2.0,
    max_wait_s: float = 10.0,
    poll_s: float = 0.5,
) -> bool:
    """Wait until ``path`` mtime+size stop changing for ``idle_s`` seconds.

    Returns ``True`` when the file went idle, ``False`` if the budget
    expired. Used to avoid copying a BIN file the autopilot is still writing
    until the AGT surface dwell completes and BlueOS disarms.
    """
    deadline = asyncio.get_event_loop().time() + max_wait_s
    last: tuple[float, int] | None = None
    last_change = asyncio.get_event_loop().time()
    while True:
        try:
            st = path.stat()
            cur = (st.st_mtime, st.st_size)
        except OSError:
            return False
        now = asyncio.get_event_loop().time()
        if last is None or cur != last:
            last = cur
            last_change = now
        elif now - last_change >= idle_s:
            return True
        if now >= deadline:
            return False
        await asyncio.sleep(poll_s)


# ── naming ──────────────────────────────────────────────────────────


_SLUG_BAD = re.compile(r"[^\w\s-]")
_SLUG_SPACE = re.compile(r"[\s-]+")


def _slug(value: str) -> str:
    """Filename-safe lowercase slug (mirrors StorageService._slug)."""
    s = _SLUG_BAD.sub("", value.strip().lower())
    s = _SLUG_SPACE.sub("_", s)
    return s


def slug_for_dive(dive_record: dict, dive_file: Path) -> str:
    """Slug to use as the BIN destination prefix.

    Prefers ``dive_name``; falls back to the dive id stem (e.g. ``dive_0066``)
    so the filename is never empty / ambiguous.
    """
    raw = str(dive_record.get("dive_name") or "").strip()
    if raw:
        s = _slug(raw)
        if s:
            return s
    return dive_file.stem  # e.g. "dive_0066"


def _dest_filename(slug: str, log_num: int, src_name: str) -> str:
    """``<slug>_<orig_num>.BIN`` (always-suffix per design)."""
    m = _BIN_RE.match(src_name)
    num_str = m.group(1) if m else f"{log_num:08d}"
    return f"{slug}_{num_str}.BIN"


# ── archive ─────────────────────────────────────────────────────────


def _set_archive_status(
    record: dict,
    *,
    status: str,
    files: list[str] | None = None,
    error: str | None = None,
) -> None:
    record["bin_log_status"] = status
    record["bin_log_copied_at"] = datetime.now(tz=timezone.utc).isoformat()
    if files is not None:
        record["bin_log_files"] = files
    elif "bin_log_files" not in record:
        record["bin_log_files"] = []
    if error is not None:
        record["bin_log_error"] = error
    else:
        record.pop("bin_log_error", None)


def _write_record(dive_file: Path, record: dict) -> None:
    try:
        dive_file.write_text(json.dumps(record, indent=2, default=str))
    except OSError as e:
        logger.warning("Failed to write dive record %s: %s", dive_file, e)


def _copy_one(src: Path, dest: Path) -> None:
    """Copy ``src`` to ``dest`` preserving mtime; create parent dirs."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()


async def archive_dive_bin_logs(
    dive_file: Path,
    *,
    logs_dir: Path | None = None,
) -> dict:
    """Locate this dive's BIN log(s), copy them to USB, update the record.

    Idempotent: re-running replaces ``bin_log_files`` / ``bin_log_status``
    with the latest result.  Returns a JSON-friendly status dict.
    """
    try:
        record = json.loads(dive_file.read_text())
    except OSError as e:
        logger.warning("BIN archive: cannot read %s: %s", dive_file, e)
        return {"status": "error", "error": f"read_dive: {e}"}
    except json.JSONDecodeError as e:
        logger.warning("BIN archive: invalid JSON in %s: %s", dive_file, e)
        return {"status": "error", "error": f"parse_dive: {e}"}

    src_paths = select_bin_logs_for_dive(record, logs_dir=logs_dir)
    if not src_paths:
        logger.info("BIN archive: no matching log for %s", dive_file.name)
        _set_archive_status(record, status="no_match", files=[])
        _write_record(dive_file, record)
        return {"status": "no_match", "files": []}

    usb_dir = usb_storage.get_recording_dir_if_available(USB_SUBDIR)
    if usb_dir is None:
        logger.warning(
            "BIN archive: USB unavailable; skipping copy for %s (%d files matched)",
            dive_file.name,
            len(src_paths),
        )
        _set_archive_status(record, status="skipped_no_usb", files=[])
        _write_record(dive_file, record)
        return {"status": "skipped_no_usb", "matched": len(src_paths)}

    dest_root = Path(usb_dir)
    slug = slug_for_dive(record, dive_file)

    written: list[str] = []
    last_error: str | None = None
    for src in src_paths:
        m = _BIN_RE.match(src.name)
        if not m:
            continue
        try:
            log_num = int(m.group(1))
        except ValueError:
            continue

        # Wait for the autopilot to release the file before copying.
        try:
            await wait_until_quiescent(src)
        except Exception as e:
            logger.debug("Quiescence wait error for %s: %s (continuing)", src, e)

        dest = dest_root / _dest_filename(slug, log_num, src.name)
        try:
            await asyncio.to_thread(_copy_one, src, dest)
            try:
                size = dest.stat().st_size
            except OSError:
                size = -1
            logger.info(
                "BIN archive: copied %s -> %s (%d bytes)", src.name, dest, size
            )
            written.append(str(dest))
        except Exception as e:
            last_error = f"{src.name}: {e}"
            logger.exception("BIN archive: copy failed for %s", src)

    if not written:
        _set_archive_status(
            record, status="error", files=[], error=last_error or "copy_failed"
        )
        _write_record(dive_file, record)
        return {"status": "error", "error": last_error or "copy_failed"}

    status = "ok" if last_error is None else "partial"
    _set_archive_status(record, status=status, files=written, error=last_error)
    _write_record(dive_file, record)
    return {"status": status, "files": written, "error": last_error}
