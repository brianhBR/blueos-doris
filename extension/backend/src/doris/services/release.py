"""On-deck weight-release test.

The release actuator lives on SERVO14 (Navigator relay 1) and is mirrored to
the AGT, so the only way to exercise it from BlueOS is the same way the dive
does: ask ``doris.lua`` for it.  Setting ``DORIS_RLS_TST`` above zero energises
both mirrored outputs; setting it back to zero releases them.

The parameter is the whole safety story.  Lua honours it only while the vehicle
is still in CONFIG, and drops the output about ten seconds after the last
assertion, so a lost "off" request or a browser that walks away cannot leave
the galvanic anode/cathode pair energised.  The frontend therefore re-asserts
it while the operator holds the button, exactly like the light test.
"""

import asyncio
import logging

import httpx

from ..config import blueos_services

logger = logging.getLogger(__name__)

PARAM_NAME = "DORIS_RLS_TST"
PARAM_READBACK_DELAY = 0.4


class ReleaseService:
    """Drives the release test parameter through mavlink2rest."""

    def __init__(self) -> None:
        self._param_ok: bool | None = None

    async def set_release_test(self, active: bool) -> dict:
        """Assert or clear the release test.

        Returns ``{"ok": bool, "error": str | None}``.
        """
        base = blueos_services.mavlink2rest
        payload = {
            "header": {"system_id": 255, "component_id": 0, "sequence": 0},
            "message": {
                "type": "PARAM_SET",
                "target_system": 1,
                "target_component": 1,
                "param_id": list(PARAM_NAME.ljust(16, "\x00")),
                "param_value": 1.0 if active else 0.0,
                "param_type": {"type": "MAV_PARAM_TYPE_REAL32"},
            },
        }
        try:
            logger.info("Release test PARAM_SET: active=%s", active)
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{base}/mavlink", json=payload)
            resp.raise_for_status()
        except Exception as e:
            logger.error("Release test PARAM_SET failed: %s", e)
            return {"ok": False, "error": f"Failed to send PARAM_SET: {e}"}

        if active and not self._param_ok and not await self._verify_param():
            return {
                "ok": False,
                "error": (
                    f"{PARAM_NAME} parameter not found on the flight controller. "
                    "Check that SCR_ENABLE=1 and doris.lua is loaded."
                ),
            }

        return {"ok": True, "error": None}

    async def _verify_param(self) -> bool:
        """Read the parameter back so a missing Lua script is reported, not silent."""
        base = blueos_services.mavlink2rest
        request = {
            "header": {"system_id": 255, "component_id": 0, "sequence": 0},
            "message": {
                "type": "PARAM_REQUEST_READ",
                "target_system": 1,
                "target_component": 1,
                "param_id": list(PARAM_NAME.ljust(16, "\x00")),
                "param_index": -1,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{base}/mavlink", json=request)
                resp.raise_for_status()

                await asyncio.sleep(PARAM_READBACK_DELAY)

                resp = await client.get(
                    f"{base}/mavlink/vehicles/1/components/1/messages/PARAM_VALUE"
                )
                resp.raise_for_status()
                data = resp.json()

            param_id = "".join(
                c for c in data.get("message", {}).get("param_id", []) if c != "\x00"
            )
            if param_id == PARAM_NAME:
                logger.info("%s verified on flight controller", PARAM_NAME)
                self._param_ok = True
                return True

            logger.warning(
                "%s not found (got param_id=%r instead)", PARAM_NAME, param_id
            )
        except Exception as e:
            logger.warning("%s verification failed: %s", PARAM_NAME, e)

        self._param_ok = False
        return False


release_service = ReleaseService()
