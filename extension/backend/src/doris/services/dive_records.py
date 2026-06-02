"""Helpers for reading persisted dive records (dives/dive_NNNN.json).

Kept free of web-framework imports so the logic is unit-testable without
standing up the Robyn app.
"""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_DIVE_FILE_RE = re.compile(r"^dive_(\d{4})\.json$")


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
