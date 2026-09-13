"""RadCam (br4kcam) camera setting models.

These mirror the br4kcam-manager protocol groups so a payload can be
forwarded verbatim to the camera via the manager's ``/v1/camera/control``
endpoint:

* :class:`VideoSettings`        -> ``getVencConf`` / ``setVencConf``
* :class:`BaseImageSettings`    -> ``getImageAdjustment`` / ``setImageAdjustment``
* :class:`AdvancedImageSettings`-> ``getImageAdjustmentEx`` / ``setImageAdjustmentEx``

Every field is optional so callers can send partial updates and reads can
tolerate the extra keys the camera returns (``extra="ignore"``).  Field
aliases are the camera's native JSON keys; dump with ``by_alias=True`` when
sending to the camera and when persisting presets, so preset files stay
portable to anyone speaking the raw protocol.

The camera exposes ISO/gain rather than an ISO number, has no EV exposure
compensation (mode + time instead), and no focus controls (fixed-focus
camera) -- so those dummy UI concepts are intentionally absent here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _CameraModel(BaseModel):
    """Base for camera setting groups: alias-aware, ignores extra keys."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class VideoResolution(BaseModel):
    """One supported resolution entry from the camera's ``pixel_list``."""

    model_config = ConfigDict(extra="ignore")

    width: int
    height: int


class VideoSettings(_CameraModel):
    """Video encoder settings (``getVencConf`` / ``setVencConf``)."""

    # Stream channel: 0 Main, 1 Auxiliary, 2 Third.
    channel: int | None = None
    # Encoding profile: 0 Baseline, 1 Main, 2 High.
    encode_profile: int | None = None
    # Video codec: 1 H.264, 5 H.265.
    encode_type: int | None = None
    # Supported resolutions (read-only, reported by the camera).
    pixel_list: list[VideoResolution] | None = None
    pic_width: int | None = None
    pic_height: int | None = None
    # Rate control: 0 VBR, 1 CBR.
    rc_mode: int | None = None
    # Bitrate in kbps.
    bitrate: int | None = None
    # Maximum supported frame rate (read-only).
    max_framerate: int | None = None
    frame_rate: int | None = None
    # I-frame interval.
    gop: int | None = None


class BaseImageSettings(_CameraModel):
    """Base image settings (``getImageAdjustment`` / ``setImageAdjustment``).

    Numeric tone/colour fields are 0-255 unless noted.
    """

    hue: int | None = None
    brightness: int | None = None
    sharpness: int | None = None
    contrast: int | None = None
    saturation: int | None = None
    gamma: int | None = None
    # Backlight compensation.
    blc_level: int | None = None

    # Exposure.
    max_exposure: int | None = None
    # 0 Auto, 1 Manual.
    auto_exposure_ex: int | None = Field(default=None, alias="auto_exposureEx")
    # 0 HighLight priority, 1 LowLight priority.
    auto_exposure_strategy_mode: int | None = Field(default=None, alias="AE_strategy_mode")
    # Manual exposure time as the 'x' in T = 1/x.
    exposure_time: int | None = None

    # White balance. 0 Auto, 1 Manual.
    auto_awb: int | None = None
    awb_red: int | None = None
    awb_green: int | None = None
    awb_blue: int | None = None
    # White-balance scene: 0 Scene1, 1 Scene2.
    awb_auto_mode: int | None = None
    awb_style_red: int | None = None
    awb_style_green: int | None = None
    awb_style_blue: int | None = None

    # Gain. auto_gain_mode: 0 Auto, 1 Manual.
    auto_gain_mode: int | None = None
    auto_d_gain_max: int | None = Field(default=None, alias="auto_DGain_max")
    auto_a_gain_max: int | None = Field(default=None, alias="auto_AGain_max")
    max_sys_gain: int | None = None
    manual_a_gain_enable: int | None = Field(default=None, alias="manual_AGain_enable")
    manual_a_gain: int | None = Field(default=None, alias="manual_AGain")
    manual_d_gain_enable: int | None = Field(default=None, alias="manual_DGain_enable")
    manual_d_gain: int | None = Field(default=None, alias="manual_DGain")

    # Misc.
    anti_fog: int | None = Field(default=None, alias="antiFog")
    frame_turbo_pro: int | None = Field(default=None, alias="frameTurbo_pro")
    # NOTE: base sceneMode is reportedly non-functional; prefer the advanced one.
    scene_mode: int | None = Field(default=None, alias="sceneMode")
    # Image rotation: 0=0deg, 1=90, 2=180, 3=270.
    rotate: int | None = None
    # Set to 1 to restore all base defaults.
    set_default: int | None = None


class AdvancedImageSettings(_CameraModel):
    """Advanced image settings (``getImageAdjustmentEx`` / ``setImageAdjustmentEx``)."""

    mirror: int | None = None
    flip: int | None = None
    # 0 NTSC, 1 PAL.
    power_freq: int | None = None
    # 0 Colour, 1 Auto (day/night).
    color_black: int | None = None
    # 0 VideoDetection, 1 TimeControl, 2 PhotosensitiveDetection.
    infr_detect_mode: int | None = None
    sens_day_to_night: int | None = None
    sens_night_to_day: int | None = None
    infr_day_h: int | None = None
    infr_day_m: int | None = None
    infr_night_h: int | None = None
    infr_night_m: int | None = None

    lens_correction: int | None = None
    wdr_level: int | None = None
    ircut_level: int | None = None
    ldr_level: int | None = None

    # Light / IR control.
    led_control_mode: int | None = None
    lamp_type: int | None = None
    led_control_avail: int | None = None
    ir_level: int | None = None
    led_level: int | None = None
    led_control: int | None = None

    # Aperture. iris_level is read-only.
    auto_iris: int | None = None
    iris_level: int | None = Field(default=None, alias="irisLevel")

    # Noise reduction.
    noise_reduction: int | None = Field(default=None, alias="noiseReduction")
    two_d_nr_level: int | None = Field(default=None, alias="_2DNR_level")

    wdr_sensor: int | None = None
    wdr_level_sensor: int | None = None
    hlc_enable: int | None = None
    # Slow shutter (camera spells the key "low_farme_rate").
    low_farme_rate: int | None = None
    # 0 Close, 1 Auto, 2 50Hz, 3 60Hz.
    anti_flicker: int | None = None
    # 0 IPC, 1 FaceCapture, 2 LicensePlateCapture.
    scene_mode: int | None = None
    # Custom one-push auto white balance trigger (0/1).
    once_awb: int | None = Field(default=None, alias="onceAWB")
    # Set to 1 to restore all advanced defaults.
    set_default: int | None = None


class CameraSettingsBundle(BaseModel):
    """The three protocol groups together (a full camera setting snapshot)."""

    model_config = ConfigDict(populate_by_name=True)

    video: VideoSettings = Field(default_factory=VideoSettings)
    base: BaseImageSettings = Field(default_factory=BaseImageSettings)
    advanced: AdvancedImageSettings = Field(default_factory=AdvancedImageSettings)


class CameraPreset(CameraSettingsBundle):
    """A named, persisted camera setting bundle.

    Serialised with ``by_alias=True`` so the ``video``/``base``/``advanced``
    payloads use the camera's native keys and re-apply directly.
    """

    name: str
    camera_model: str = "radcam"
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class CameraPresetSummary(BaseModel):
    """Lightweight listing entry for the preset library."""

    name: str
    camera_model: str = "radcam"
    created_at: datetime
    updated_at: datetime


class ActivePreset(BaseModel):
    """Pointer to the preset auto-applied at startup and dive start."""

    name: str | None = None
    updated_at: datetime = Field(default_factory=_utc_now)
