"""Proxy client for the br4kcam-manager ("4K Cam Manager") extension.

DORIS does not talk to the RadCam directly for settings; it forwards
requests to the manager's REST API, which owns camera discovery (via the
Mavlink Camera Manager) and the camera-native HTTP protocol.

Manager endpoints used:

* ``GET  {base}/v1/camera/list``    -> map/list of cameras keyed by UUID
* ``POST {base}/v1/camera/control`` -> ``{camera_uuid, action, json}``

The manager auto-re-reads after each ``set*`` and returns the fresh camera
state, so the ``set_*`` helpers here return the updated group.
"""

import logging
from typing import Any

import httpx

from ..config import blueos_services
from ..models.camera import (
    AdvancedImageSettings,
    BaseImageSettings,
    VideoSettings,
)

logger = logging.getLogger(__name__)

# Fields the camera reports but will not accept on a set; strip before sending.
_VIDEO_READONLY = frozenset({"pixel_list", "max_framerate"})
_ADVANCED_READONLY = frozenset({"irisLevel"})


class Br4kcamError(RuntimeError):
    """Raised when the manager is unreachable or returns an error."""


class Br4kcamClient:
    """Thin async client over the RadCam manager REST API.

    Builds absolute request URLs from ``base_url`` rather than relying on an
    httpx ``base_url`` join, because the manager is reached through the
    blueos-core nginx proxy at a path prefix (``/extensionv2/<slug>``) that a
    standard absolute-path join would otherwise drop.
    """

    def __init__(self, base_url: str | None = None, timeout: float = 15.0):
        self._base = (base_url or blueos_services.br4kcam).rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    async def close(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    # ── discovery ────────────────────────────────────────────────
    async def list_cameras(self) -> dict[str, Any]:
        """Return the manager's camera list (raw)."""
        try:
            r = await self._client.get(self._url("/v1/camera/list"))
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - surface a typed error
            raise Br4kcamError(f"Failed to list cameras: {e}") from e

    async def get_primary_camera_uuid(self) -> str | None:
        """Resolve the first known camera UUID, or ``None`` if none."""
        data = await self.list_cameras()
        if isinstance(data, dict):
            # /list returns a map keyed by camera UUID.
            for key in data:
                return key
            return None
        if isinstance(data, list):
            for cam in data:
                if isinstance(cam, dict):
                    uuid = cam.get("camera_uuid") or cam.get("uuid") or cam.get("id")
                    if uuid:
                        return str(uuid)
            return None
        return None

    # ── low-level control ────────────────────────────────────────
    async def _control(
        self, camera_uuid: str, action: str, json: dict | None = None
    ) -> Any:
        body: dict[str, Any] = {"camera_uuid": camera_uuid, "action": action}
        if json is not None:
            body["json"] = json
        try:
            r = await self._client.post(self._url("/v1/camera/control"), json=body)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            raise Br4kcamError(f"control {action} failed: {e}") from e

    # ── reads ────────────────────────────────────────────────────
    async def get_video(self, camera_uuid: str, channel: int = 0) -> VideoSettings:
        raw = await self._control(camera_uuid, "getVencConf", {"channel": channel})
        return VideoSettings.model_validate(raw if isinstance(raw, dict) else {})

    async def get_base(self, camera_uuid: str) -> BaseImageSettings:
        raw = await self._control(camera_uuid, "getImageAdjustment")
        return BaseImageSettings.model_validate(raw if isinstance(raw, dict) else {})

    async def get_advanced(self, camera_uuid: str) -> AdvancedImageSettings:
        raw = await self._control(camera_uuid, "getImageAdjustmentEx")
        return AdvancedImageSettings.model_validate(raw if isinstance(raw, dict) else {})

    # ── writes (manager re-reads and returns fresh state) ─────────
    async def set_video(
        self, camera_uuid: str, settings: VideoSettings
    ) -> VideoSettings:
        payload = settings.model_dump(by_alias=True, exclude_none=True)
        for key in _VIDEO_READONLY:
            payload.pop(key, None)
        if not payload:
            return await self.get_video(camera_uuid, channel=settings.channel or 0)
        payload.setdefault("channel", settings.channel or 0)
        raw = await self._control(camera_uuid, "setVencConf", payload)
        return VideoSettings.model_validate(raw if isinstance(raw, dict) else {})

    async def set_base(
        self, camera_uuid: str, settings: BaseImageSettings
    ) -> BaseImageSettings:
        payload = settings.model_dump(by_alias=True, exclude_none=True)
        if not payload:
            return await self.get_base(camera_uuid)
        raw = await self._control(camera_uuid, "setImageAdjustment", payload)
        return BaseImageSettings.model_validate(raw if isinstance(raw, dict) else {})

    async def set_advanced(
        self, camera_uuid: str, settings: AdvancedImageSettings
    ) -> AdvancedImageSettings:
        payload = settings.model_dump(by_alias=True, exclude_none=True)
        for key in _ADVANCED_READONLY:
            payload.pop(key, None)
        if not payload:
            return await self.get_advanced(camera_uuid)
        raw = await self._control(camera_uuid, "setImageAdjustmentEx", payload)
        return AdvancedImageSettings.model_validate(raw if isinstance(raw, dict) else {})

    # ── wrappers ─────────────────────────────────────────────────
    async def apply_recommended(self, camera_uuid: str) -> Any:
        return await self._control(camera_uuid, "setRecommendedCameraSettings")

    async def restart(self, camera_uuid: str) -> Any:
        return await self._control(camera_uuid, "restart")
