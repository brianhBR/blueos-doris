"""Persistent operator policy for AGT-controlled payload shutdown."""

from __future__ import annotations

import json
import logging

from ..config import settings
from .dive_records import write_json_atomic
from .storage import DATA_ROOT

logger = logging.getLogger(__name__)

POLICY_PATH = DATA_ROOT / "configurations" / "agt_shutdown_policy.json"


def get_shutdown_policy() -> dict[str, bool]:
    """Return the persisted bench override and effective shutdown policy."""
    bench_mode = not settings.agt_shutdown_enabled
    try:
        payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        persisted = payload.get("bench_mode")
        if isinstance(persisted, bool):
            bench_mode = persisted
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError) as error:
        logger.warning("Ignoring invalid AGT shutdown policy: %s", error)
    return {
        "bench_mode": bench_mode,
        "automatic_payload_shutdown": not bench_mode,
    }


def automatic_payload_shutdown_enabled() -> bool:
    """Whether BlueOS may ACK an AGT shutdown request and power off."""
    return get_shutdown_policy()["automatic_payload_shutdown"]


def set_bench_mode(enabled: bool) -> dict[str, bool]:
    """Persist the bench override atomically and return the effective policy."""
    write_json_atomic(POLICY_PATH, {"bench_mode": enabled})
    return {
        "bench_mode": enabled,
        "automatic_payload_shutdown": not enabled,
    }
