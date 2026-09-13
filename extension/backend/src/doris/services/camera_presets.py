"""Camera preset orchestration.

Ties the br4kcam-manager proxy (:mod:`.br4kcam`) to on-disk preset
persistence (:class:`.storage.StorageService`):

* read the live camera setting snapshot,
* apply an arbitrary bundle / named preset to the camera,
* snapshot the current camera state into a named preset,
* apply the "active" preset (used at startup and dive start).
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

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


# ── DORIS hardware defaults ──────────────────────────────────────────
#
# A handful of settings are dictated by this vehicle's hardware.  DORIS supplies
# sensible defaults for them, but treats those defaults as *fallbacks*: they
# fill in only the keys a preset/apply didn't specify, so an advanced user can
# still override any of them by including the key in a preset or hand-edited
# JSON.  With no active preset they are applied as a baseline at startup and
# dive start (see :func:`apply_active_preset_best_effort`).  They are hidden
# from the experimentation UI simply because there is no control for them yet.
#
#   H. Day/Night & IR-Cut : color_black=0 -> colour/day, IR-cut filter engaged.
#   I. Light / IR LED      : led_control=2 -> illuminator disabled (no LEDs).
#   J. Aperture / Iris     : auto_iris=1  -> iris disabled; fixed-aperture lens.
#   M. Scene Mode          : scene_mode=0 (advanced, IPC) and sceneMode=0 (base)
#                            -> plain general video capture.
DORIS_BASE_DEFAULTS = BaseImageSettings(scene_mode=0)
DORIS_ADVANCED_DEFAULTS = AdvancedImageSettings(
    color_black=0,
    led_control=2,
    auto_iris=1,
    scene_mode=0,
)


def _fill_missing_defaults(model, defaults):
    """Return a copy of ``model`` with ``defaults`` filling only unset fields.

    Values already present in ``model`` win, so a preset or hand-edited JSON
    that specifies one of these keys is honored; the defaults just supply the
    keys the caller left out.
    """
    data = defaults.model_dump(exclude_none=True)
    data.update(model.model_dump(exclude_none=True))
    return type(model)(**data)


class NoCameraError(Br4kcamError):
    """Raised when no camera is known to the manager."""


class CameraPresetService:
    """Applies/reads/persists RadCam settings and presets."""

    def __init__(self, timeout: float = 15.0) -> None:
        self.client = Br4kcamClient(timeout=timeout)
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
        # Fill in DORIS's hardware defaults for any of these settings the caller
        # didn't specify, so a preset that omits them still gets a sane value.
        # An explicit value in the bundle/preset/JSON is preserved, so an
        # advanced user can override them by editing the JSON directly.
        base = _fill_missing_defaults(bundle.base, DORIS_BASE_DEFAULTS)
        advanced = _fill_missing_defaults(bundle.advanced, DORIS_ADVANCED_DEFAULTS)

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

    async def apply_baseline_defaults(
        self, camera_uuid: str | None = None
    ) -> CameraSettingsBundle:
        """Apply DORIS's hardware defaults as a baseline.

        Used at startup and dive start when no preset is active, so the camera
        still lands in a sane default state.  When a preset *is* active its
        values take precedence (see :func:`apply_active_preset_best_effort`).
        """
        uuid = await self._require_uuid(camera_uuid)
        base = await self.client.set_base(
            uuid, DORIS_BASE_DEFAULTS.model_copy(deep=True)
        )
        advanced = await self.client.set_advanced(
            uuid, DORIS_ADVANCED_DEFAULTS.model_copy(deep=True)
        )
        return CameraSettingsBundle(video=VideoSettings(), base=base, advanced=advanced)

    async def trigger_awb(
        self, camera_uuid: str | None = None
    ) -> AdvancedImageSettings:
        """Fire a one-push auto white balance on the camera.

        Uses the advanced ``onceAWB`` trigger, which tells the RadCam to run a
        single auto-white-balance convergence against the current (lit) scene
        and then hold that balance.  Called by the Lua dive script once the
        bottom lights come on.
        """
        uuid = await self._require_uuid(camera_uuid)
        return await self.client.set_advanced(
            uuid, AdvancedImageSettings(once_awb=1)
        )

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


async def record_camera_sample(
    phase: str,
    *,
    dives_dir: Path,
    dive_file: Path | None = None,
    include_error_sample: bool = True,
    timeout: float = 6.0,
    logger_: logging.Logger = logger,
) -> None:
    """Snapshot the live camera settings into a dive record (best-effort).

    Reads the full video/base/advanced bundle from the br4kcam-manager and
    appends it under ``camera_settings_samples`` on the target dive record so
    the footage can be interpreted after the dive (the settings themselves are
    not carried in the video stream).

    Never raises: a missing camera or unreachable manager just logs.  When
    ``include_error_sample`` is set, an unreachable camera still records a small
    ``{"error": ...}`` marker so post-analysis can tell the camera was offline
    at that phase; ``recovery`` passes ``False`` because the payload may already
    be powering down and a failure there is expected, not noteworthy.
    """
    sample: dict = {
        "phase": phase,
        "sampled_at": datetime.now(timezone.utc).isoformat(),
    }
    service = CameraPresetService(timeout=timeout)
    try:
        uuid = await service.client.get_primary_camera_uuid()
        if not uuid:
            raise NoCameraError("No camera found via br4kcam-manager")
        bundle = await service.read_live(uuid)
        sample["camera_uuid"] = uuid
        sample["video"] = bundle.video.model_dump(by_alias=True, exclude_none=True)
        sample["base"] = bundle.base.model_dump(by_alias=True, exclude_none=True)
        sample["advanced"] = bundle.advanced.model_dump(by_alias=True, exclude_none=True)
    except Exception as e:  # noqa: BLE001 - best-effort, never propagate
        if not include_error_sample:
            logger_.info("Camera sample (%s) skipped: %s", phase, e)
            return
        sample["error"] = str(e)
        logger_.warning("Camera sample (%s) failed: %s", phase, e)
    finally:
        await service.close()

    try:
        from .dive_records import append_camera_sample_to_dive

        written = append_camera_sample_to_dive(dives_dir, sample, dive_file=dive_file)
        if written is not None:
            logger_.info("Camera sample (%s) recorded to %s", phase, written.name)
        else:
            logger_.info(
                "Camera sample (%s): no active dive record to attach", phase
            )
    except Exception as e:  # noqa: BLE001
        logger_.warning("Camera sample (%s) append failed: %s", phase, e)


async def apply_active_preset_best_effort(logger_: logging.Logger) -> None:
    """Best-effort apply of the active preset (startup / dive start).

    Never raises: a missing camera or unreachable manager just logs and
    returns so it can't block startup or a dive.
    """
    service = CameraPresetService()
    try:
        applied = await service.apply_active()
        if applied:
            # The preset is authoritative; apply_bundle already filled any
            # H/I/J/M keys it omitted with DORIS's defaults.
            logger_.info("Applied active camera preset: %s", applied)
        else:
            # No active preset: land the camera in DORIS's baseline defaults.
            await service.apply_baseline_defaults()
            logger_.info("Applied baseline camera defaults (no active preset)")
    except Br4kcamError as e:
        logger_.info("Active camera preset / defaults not applied: %s", e)
    except Exception as e:  # noqa: BLE001
        logger_.warning("Active camera preset / defaults failed: %s", e)
    finally:
        await service.close()
