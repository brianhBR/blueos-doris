"""Camera preset orchestration.

Ties the br4kcam-manager proxy (:mod:`.br4kcam`) to on-disk preset
persistence (:class:`.storage.StorageService`):

* read the live camera setting snapshot,
* apply an arbitrary bundle / named preset to the camera,
* snapshot the current camera state into a named preset,
* apply the "active" preset (used at startup and dive start).
"""

import logging

from ..models.camera import (
    AdvancedImageSettings,
    BaseImageSettings,
    CameraPreset,
    CameraSettingsBundle,
    VideoSettings,
)
from .br4kcam import Br4kcamClient, Br4kcamError
from .storage import StorageService

logger = logging.getLogger(__name__)


class NoCameraError(Br4kcamError):
    """Raised when no camera is known to the manager."""


class CameraPresetService:
    """Applies/reads/persists RadCam settings and presets."""

    def __init__(self) -> None:
        self.client = Br4kcamClient()
        self.storage = StorageService()

    async def close(self) -> None:
        await self.client.close()

    async def _require_uuid(self, camera_uuid: str | None = None) -> str:
        uuid = camera_uuid or await self.client.get_primary_camera_uuid()
        if not uuid:
            raise NoCameraError("No camera found via br4kcam-manager")
        return uuid

    async def read_live(self, camera_uuid: str | None = None) -> CameraSettingsBundle:
        """Read the current video + base + advanced settings from the camera."""
        uuid = await self._require_uuid(camera_uuid)
        return CameraSettingsBundle(
            video=await self.client.get_video(uuid),
            base=await self.client.get_base(uuid),
            advanced=await self.client.get_advanced(uuid),
        )

    async def apply_bundle(
        self, bundle: CameraSettingsBundle, camera_uuid: str | None = None
    ) -> CameraSettingsBundle:
        """Push each non-empty group to the camera, returning the fresh state."""
        uuid = await self._require_uuid(camera_uuid)

        video = bundle.video
        base = bundle.base
        advanced = bundle.advanced

        if video.model_dump(exclude_none=True, exclude={"channel"}):
            video = await self.client.set_video(uuid, video)
        if base.model_dump(exclude_none=True):
            base = await self.client.set_base(uuid, base)
        if advanced.model_dump(exclude_none=True):
            advanced = await self.client.set_advanced(uuid, advanced)

        return CameraSettingsBundle(video=video, base=base, advanced=advanced)

    async def apply_partial(
        self,
        *,
        video: VideoSettings | None = None,
        base: BaseImageSettings | None = None,
        advanced: AdvancedImageSettings | None = None,
        camera_uuid: str | None = None,
    ) -> CameraSettingsBundle:
        """Apply an arbitrary partial update (experimentation endpoint)."""
        bundle = CameraSettingsBundle(
            video=video or VideoSettings(),
            base=base or BaseImageSettings(),
            advanced=advanced or AdvancedImageSettings(),
        )
        return await self.apply_bundle(bundle, camera_uuid)

    async def snapshot_to_preset(
        self, name: str, camera_uuid: str | None = None
    ) -> CameraPreset:
        """Read live settings and persist them as a named preset."""
        bundle = await self.read_live(camera_uuid)
        preset = CameraPreset(
            name=name,
            video=bundle.video,
            base=bundle.base,
            advanced=bundle.advanced,
        )
        return await self.storage.save_camera_preset(preset)

    async def apply_preset(
        self, name: str, camera_uuid: str | None = None
    ) -> CameraSettingsBundle | None:
        """Load a saved preset and apply it to the camera."""
        preset = await self.storage.load_camera_preset(name)
        if preset is None:
            return None
        return await self.apply_bundle(
            CameraSettingsBundle(
                video=preset.video, base=preset.base, advanced=preset.advanced
            ),
            camera_uuid,
        )

    async def apply_active(self) -> str | None:
        """Apply the active preset if one is set. Returns the applied name."""
        active = await self.storage.get_active_preset()
        if not active.name:
            return None
        result = await self.apply_preset(active.name)
        if result is None:
            logger.warning("Active preset %r not found on disk", active.name)
            return None
        return active.name


async def apply_active_preset_best_effort(logger_: logging.Logger) -> None:
    """Best-effort apply of the active preset (startup / dive start).

    Never raises: a missing camera or unreachable manager just logs and
    returns so it can't block startup or a dive.
    """
    service = CameraPresetService()
    try:
        applied = await service.apply_active()
        if applied:
            logger_.info("Applied active camera preset: %s", applied)
    except Br4kcamError as e:
        logger_.info("Active camera preset not applied: %s", e)
    except Exception as e:  # noqa: BLE001
        logger_.warning("Active camera preset apply failed: %s", e)
    finally:
        await service.close()
