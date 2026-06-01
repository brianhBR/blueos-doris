"""Post-dive dive-data CSV export to USB.

After a dive ends (completed or cancelled), parse the dive's primary
``.mcap`` and write the comprehensive dive-data CSV to the external USB
drive.  This mirrors :mod:`binlog` (which archives ArduPilot ``.BIN``
logs to USB on the same hook) and exists for two reasons:

1. **Latency.** Parsing a multi-million-message ``.mcap`` takes tens of
   seconds on the vehicle's Raspberry Pi.  Doing it once in the
   background at dive end means the operator's "Export dive data" button
   can serve the pre-generated file instantly instead of re-parsing on
   the request path.
2. **Durability.** The CSV lands on the USB stick alongside the BIN logs
   and video, so it survives even when no laptop is connected and shows
   up automatically in the Data tab (USB files are indexed as media).

The ``.mcap`` remains the source of truth: the export is best-effort and
idempotent, and the on-demand route always falls back to a fresh parse
when no cached CSV is available.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from . import usb_storage
from .binlog import slug_for_dive, wait_until_quiescent

logger = logging.getLogger(__name__)

USB_SUBDIR = "dive_data"


def _data_root() -> Path:
    return Path(os.environ.get("DORIS_DATA_ROOT", "/tmp/storage"))


def _set_export_status(
    record: dict,
    *,
    status: str,
    file: str | None = None,
    error: str | None = None,
) -> None:
    record["csv_export_status"] = status
    record["csv_exported_at"] = datetime.now(tz=timezone.utc).isoformat()
    if file is not None:
        record["csv_export_file"] = file
    elif status != "ok":
        # Drop a stale path when the latest attempt did not produce a file
        # so the route doesn't try to serve a file that is gone.
        record.pop("csv_export_file", None)
    if error is not None:
        record["csv_export_error"] = error
    else:
        record.pop("csv_export_error", None)


def _write_record(dive_file: Path, record: dict) -> None:
    import json

    try:
        dive_file.write_text(json.dumps(record, indent=2, default=str))
    except OSError as e:
        logger.warning("CSV export: failed to write dive record %s: %s", dive_file, e)


def _write_text_atomic(dest: Path, text: str) -> None:
    """Write ``text`` (UTF-8) to ``dest`` atomically; create parent dirs."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()


async def export_dive_csv_to_usb(dive_file: Path) -> dict:
    """Parse this dive's ``.mcap`` and write its dive-data CSV to USB.

    Idempotent: re-running replaces ``csv_export_*`` on the dive record
    with the latest result.  Returns a JSON-friendly status dict.  Never
    raises -- all failures are captured in the returned ``status``.
    """
    import json

    # Lazy imports to avoid import cycles (storage imports services too).
    from .mcap_telemetry import (
        McapSummary,
        build_dive_csv,
        map_dive_stem_to_largest_mcap,
        summarize_mcap,
    )
    from .storage import (
        _load_dive_windows,
        media_download_id_from_abs_path,
    )

    try:
        record = json.loads(dive_file.read_text())
    except OSError as e:
        logger.warning("CSV export: cannot read %s: %s", dive_file, e)
        return {"status": "error", "error": f"read_dive: {e}"}
    except json.JSONDecodeError as e:
        logger.warning("CSV export: invalid JSON in %s: %s", dive_file, e)
        return {"status": "error", "error": f"parse_dive: {e}"}

    data_root = _data_root()
    dive_id = dive_file.stem  # e.g. "dive_0066"

    # Locate this dive's primary .mcap via the same window mapping the
    # history list and on-demand export use.
    try:
        windows = _load_dive_windows(data_root)
        mcap_map = map_dive_stem_to_largest_mcap(data_root, windows)
    except Exception as e:
        logger.warning("CSV export: mcap mapping failed for %s: %s", dive_id, e)
        mcap_map = {}
    mcap_path = mcap_map.get(dive_id)

    rel: str | None = None
    summary = McapSummary()
    if mcap_path is not None:
        rel = media_download_id_from_abs_path(mcap_path, data_root)
        # The recorder may still be flushing the file at dive end; wait for
        # it to go idle before reading so we don't parse a half-written log.
        with contextlib.suppress(Exception):
            await wait_until_quiescent(mcap_path)
        try:
            summary = await asyncio.to_thread(summarize_mcap, mcap_path)
        except Exception as e:
            logger.warning("CSV export: summarize failed for %s: %s", mcap_path, e)
            _set_export_status(record, status="error", error=f"summarize: {e}")
            _write_record(dive_file, record)
            return {"status": "error", "error": f"summarize: {e}"}
    else:
        logger.info("CSV export: no .mcap matched %s", dive_id)

    try:
        csv_text = build_dive_csv(record, summary, rel)
    except Exception as e:
        logger.warning("CSV export: build_dive_csv failed for %s: %s", dive_id, e)
        _set_export_status(record, status="error", error=f"build: {e}")
        _write_record(dive_file, record)
        return {"status": "error", "error": f"build: {e}"}

    usb_dir = usb_storage.get_recording_dir_if_available(USB_SUBDIR)
    if usb_dir is None:
        logger.warning("CSV export: USB unavailable; skipping write for %s", dive_id)
        _set_export_status(record, status="skipped_no_usb")
        _write_record(dive_file, record)
        return {"status": "skipped_no_usb"}

    slug = slug_for_dive(record, dive_file)
    dest = Path(usb_dir) / f"{slug}_dive_data.csv"
    try:
        await asyncio.to_thread(_write_text_atomic, dest, csv_text)
    except Exception as e:
        logger.exception("CSV export: write failed for %s", dive_id)
        _set_export_status(record, status="error", error=f"write: {e}")
        _write_record(dive_file, record)
        return {"status": "error", "error": f"write: {e}"}

    rows = len(summary.frames)
    logger.info(
        "CSV export: wrote %s (%d rows, %d bytes)",
        dest,
        rows,
        len(csv_text.encode("utf-8")),
    )
    _set_export_status(record, status="ok", file=str(dest))
    record["csv_export_rows"] = rows
    _write_record(dive_file, record)
    return {"status": "ok", "file": str(dest), "rows": rows}


def find_cached_csv(record: dict) -> Path | None:
    """Return the pre-generated USB CSV for this dive if it still exists."""
    raw = record.get("csv_export_file")
    if not isinstance(raw, str) or not raw.strip():
        return None
    p = Path(raw)
    try:
        if p.is_file() and p.stat().st_size > 0:
            return p
    except OSError:
        return None
    return None
