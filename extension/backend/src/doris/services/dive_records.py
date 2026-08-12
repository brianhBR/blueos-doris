"""Helpers for reading persisted dive records (dives/dive_NNNN.json).

Kept free of web-framework imports so the logic is unit-testable without
standing up the Robyn app.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DIVE_FILE_RE = re.compile(r"^dive_(\d{4})\.json$")


def write_json_atomic(path: Path, payload: dict) -> None:
    """Write JSON so an interrupted write cannot truncate the original.

    Payload power can be cut moments after a dive ends, and a half-written dive
    record or mission state would be unreadable on the next boot.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    os.replace(tmp, path)


def update_active_dive_record(dives_dir: Path, new_status: str) -> Path | None:
    """Close the most recent active dive record. Returns its path, or None.

    The record is also marked as awaiting post-dive processing, which is what
    puts a "Process Dive" button on it in the UI.
    """
    dives_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[tuple[int, Path]] = []
    for f in dives_dir.iterdir():
        m = _DIVE_FILE_RE.match(f.name)
        if m:
            candidates.append((int(m.group(1)), f))
    candidates.sort(reverse=True)

    for _, dive_file in candidates:
        try:
            record = json.loads(dive_file.read_text())
            if record.get("status") == "active":
                record["status"] = new_status
                record["ended_at"] = datetime.now(tz=timezone.utc).isoformat()
                record.setdefault("processing_state", "pending")
                write_json_atomic(dive_file, record)
                logger.info("Dive record updated: %s -> %s", dive_file.name, new_status)
                return dive_file
        except Exception as e:
            logger.warning("Error reading %s: %s", dive_file.name, e)
    return None


def set_mission_terminal_status(mission_state_path: Path, new_status: str) -> None:
    """Mark mission_state.json completed or cancelled, if not already terminal."""
    if new_status not in ("cancelled", "completed"):
        return
    if not mission_state_path.exists():
        return
    try:
        ms = json.loads(mission_state_path.read_text())
        if ms.get("status") in ("completed", "cancelled"):
            return
        ms["status"] = new_status
        key = "cancelled_at" if new_status == "cancelled" else "completed_at"
        ms[key] = datetime.now(tz=timezone.utc).isoformat()
        write_json_atomic(mission_state_path, ms)
    except Exception as e:
        logger.warning("Failed to set mission status %s: %s", new_status, e)


def find_latest_active_dive_record(dives_dir: Path) -> dict | None:
    """Return the highest-numbered dive record still marked 'active'.

    The persisted dive record reliably survives a vehicle restart, whereas
    the derived mission state can be lost, so this is used to reconstruct
    the active dive's configuration name for the banner (issue #38).  The
    returned dict includes a ``_dive_file`` key with the source filename.
    Returns ``None`` when there is no active record.
    """
    if not dives_dir.is_dir():
        return None
    candidates: list[tuple[int, Path]] = []
    for f in dives_dir.iterdir():
        m = _DIVE_FILE_RE.match(f.name)
        if m:
            candidates.append((int(m.group(1)), f))
    candidates.sort(reverse=True)
    for _, dive_file in candidates:
        try:
            record = json.loads(dive_file.read_text())
        except Exception as e:
            logger.warning("Error reading %s: %s", dive_file.name, e)
            continue
        if record.get("status") == "active":
            record["_dive_file"] = dive_file.name
            return record
    return None
