// Control metadata for the RadCam advanced settings editor.
//
// Maps each camera-native key (as forwarded by the br4kcam-manager) to a
// friendly label and the right input control — a dropdown for enums, a slider
// for bounded numeric ranges, a plain number otherwise. Ranges/enums come from
// the radcam-manager protocol (see docs/camera-settings.md). Keys DORIS pins to
// fixed hardware defaults (H/I/J/M) are intentionally omitted; unknown keys the
// camera might report fall back to a number input via `cameraFieldMeta`.

export type CameraControlKind = 'slider' | 'select' | 'number'

// Protocol groups the editor writes to.  ``video`` is the encoder-config
// group (getVencConf/setVencConf); ``base``/``advanced`` are the two image
// (ISP) groups.
export type CameraGroup = 'base' | 'advanced' | 'video'

export interface CameraFieldOption {
  value: number
  label: string
}

export interface CameraFieldMeta {
  label: string
  kind: CameraControlKind
  min?: number
  max?: number
  step?: number
  unit?: string
  options?: CameraFieldOption[]
  help?: string
}

// Helper builders keep the table below terse.
const range = (label: string, help?: string, max = 255): CameraFieldMeta => ({
  label, kind: 'slider', min: 0, max, step: 1, help,
})
const options = (label: string, opts: CameraFieldOption[], help?: string): CameraFieldMeta => ({
  label, kind: 'select', options: opts, help,
})
// Enable/disable where the camera uses Close=0 (disabled), Open=1 (enabled).
const enableCloseZero = (label: string, help?: string) =>
  options(label, [{ value: 0, label: 'Disabled' }, { value: 1, label: 'Enabled' }], help)
// Enable/disable where the camera uses Open=0 (enabled), Close=1 (disabled).
const enableOpenZero = (label: string, help?: string) =>
  options(label, [{ value: 0, label: 'Enabled' }, { value: 1, label: 'Disabled' }], help)

// Shutter-time enum shared by max/manual exposure (value is the x in T = 1/x s).
const SHUTTER = [12, 25, 30, 50, 60, 100, 200, 400, 800, 1000, 2000, 4000, 8000]
const shutterOpts = (extra: number[] = []): CameraFieldOption[] =>
  [...SHUTTER, ...extra].map(v => ({ value: v, label: `1/${v} s` }))

export const CAMERA_BASE_SCHEMA: Record<string, CameraFieldMeta> = {
  // Tone & colour
  hue: range('Hue', 'Colour tint / shift.'),
  brightness: range('Brightness', 'Overall image lightness.'),
  sharpness: range('Sharpness', 'Edge enhancement / apparent detail.'),
  contrast: range('Contrast', 'Difference between light and dark areas.'),
  saturation: range('Saturation', 'Colour intensity.'),
  gamma: range('Gamma', 'Midtone response / tone-curve shape.'),
  // Exposure
  blc_level: range('Backlight Compensation', 'Brightens a subject lit from behind.'),
  max_exposure: options('Max Exposure', shutterOpts(), 'Longest exposure the auto-exposure loop may use.'),
  auto_exposureEx: options('Auto Exposure Mode', [
    { value: 0, label: 'Auto' }, { value: 1, label: 'Manual' },
  ]),
  AE_strategy_mode: options('AE Strategy', [
    { value: 0, label: 'Highlight priority' }, { value: 1, label: 'Lowlight priority' },
  ], 'Metering priority in auto exposure.'),
  exposure_time: options('Manual Exposure Time', shutterOpts([10000, 34464]),
    'Fixed shutter time; used only in manual exposure.'),
  // White balance
  auto_awb: options('Auto White Balance', [
    { value: 0, label: 'Auto' }, { value: 1, label: 'Manual' },
  ]),
  awb_red: range('WB Red Gain', 'Manual red channel gain (manual WB).'),
  awb_green: range('WB Green Gain', 'Manual green channel gain (manual WB).'),
  awb_blue: range('WB Blue Gain', 'Manual blue channel gain (manual WB).'),
  awb_auto_mode: options('WB Scene', [
    { value: 0, label: 'Scene 1' }, { value: 1, label: 'Scene 2' },
  ], 'Auto white-balance scene preset.'),
  awb_style_red: range('WB Style Red', 'Red bias applied on top of the auto result.'),
  awb_style_green: range('WB Style Green', 'Green bias applied on top of the auto result.'),
  awb_style_blue: range('WB Style Blue', 'Blue bias applied on top of the auto result.'),
  // Gain / ISO
  auto_gain_mode: options('Auto Gain Mode', [
    { value: 0, label: 'Auto' }, { value: 1, label: 'Manual' },
  ]),
  auto_DGain_max: range('Max Auto Digital Gain', 'Ceiling for digital gain in auto mode.'),
  auto_AGain_max: range('Max Auto Analog Gain', 'Ceiling for analog (sensor) gain in auto mode.'),
  max_sys_gain: range('Max System Gain', 'Overall gain ceiling.'),
  manual_AGain_enable: enableCloseZero('Manual Analog Gain', 'Use a fixed analog gain.'),
  manual_AGain: range('Manual Analog Gain Value'),
  manual_DGain_enable: enableCloseZero('Manual Digital Gain', 'Use a fixed digital gain.'),
  manual_DGain: range('Manual Digital Gain Value'),
  // Misc
  antiFog: enableCloseZero('Anti-Fog', 'Defog / dehaze enhancement.'),
  frameTurbo_pro: options('Frame Turbo Pro', [
    { value: 0, label: 'Off' }, { value: 1, label: 'High frame rate' }, { value: 2, label: 'Ultra-high frame rate' },
  ], 'Frame-rate enhancement / turbo processing.'),
  rotate: options('Rotate', [
    { value: 0, label: '0°' }, { value: 1, label: '90°' }, { value: 2, label: '180°' }, { value: 3, label: '270°' },
  ], 'Image rotation.'),
}

export const CAMERA_ADVANCED_SCHEMA: Record<string, CameraFieldMeta> = {
  // Orientation
  mirror: enableOpenZero('Mirror', 'Horizontal flip.'),
  flip: enableOpenZero('Flip', 'Vertical flip.'),
  // Standard
  power_freq: options('Video Standard', [
    { value: 0, label: 'NTSC (60 Hz regions / 30 fps)' }, { value: 1, label: 'PAL (50 Hz regions / 25 fps)' },
  ], 'Video standard / frame-rate base (NTSC vs PAL). This is NOT the same as Anti-Flicker, which suppresses artificial-light flicker at 50/60 Hz.'),
  lens_correction: enableOpenZero('Lens Correction', 'Corrects geometric lens distortion.'),
  // WDR & highlight
  wdr_level: range('WDR Level (digital)', 'Digital wide-dynamic-range strength.'),
  wdr_sensor: enableCloseZero('WDR Sensor', 'Enable true sensor-based WDR.'),
  wdr_level_sensor: range('WDR Sensor Level', 'Strength of the sensor-based WDR.'),
  hlc_enable: enableCloseZero('Highlight Compensation (HLC)', 'Dims very bright spots.'),
  // Noise reduction
  noiseReduction: options('Noise Reduction (3D)', [
    { value: 0, label: 'Off' }, { value: 1, label: 'Low' }, { value: 2, label: 'Middle' }, { value: 3, label: 'High' },
  ], 'Temporal (3D) noise reduction.'),
  _2DNR_level: options('2D NR Level', [
    { value: 0, label: 'Low' }, { value: 1, label: 'Middle' }, { value: 2, label: 'High' },
  ], 'Spatial (single-frame) noise reduction.'),
  // Shutter & flicker
  low_farme_rate: enableCloseZero('Slow Shutter', 'Enable slow shutter for low-light scenes.'),
  anti_flicker: options('Anti-Flicker', [
    { value: 0, label: 'Off' }, { value: 1, label: 'Auto' }, { value: 2, label: '50 Hz' }, { value: 3, label: '60 Hz' },
  ], 'Reduces flicker under artificial light.'),
}

// Video-encoder fields that live in the Advanced editor (the common ones —
// resolution, frame rate, bitrate, codec, rate control — have dedicated
// controls in the main quality panel).
export const CAMERA_VIDEO_SCHEMA: Record<string, CameraFieldMeta> = {
  gop: {
    label: 'Keyframe Interval (GOP)',
    kind: 'number',
    help: 'Frames between keyframes (I-frames). Lower = more seekable / larger files; higher = smaller files.',
  },
}

/**
 * Resolve control metadata for a native key. Falls back to a plain number
 * input (with the raw key as its label) so unrecognized keys are still usable.
 */
export function cameraFieldMeta(key: string, group: CameraGroup): CameraFieldMeta {
  const table =
    group === 'base' ? CAMERA_BASE_SCHEMA
    : group === 'advanced' ? CAMERA_ADVANCED_SCHEMA
    : CAMERA_VIDEO_SCHEMA
  return table[key] ?? { label: key, kind: 'number' }
}

// ── Logical grouping for the editor ──────────────────────────────────
//
// The camera exposes settings across two protocol groups (base / advanced),
// but related controls (e.g. all the gain fields) belong together for the
// operator.  These ordered sections drive the editor layout; each field notes
// which protocol group it writes to.  Keys not listed here still render, under
// an "Other" section, so nothing the camera reports is hidden.

export interface CameraSectionField {
  key: string
  group: CameraGroup
}

export interface CameraSectionDef {
  title: string
  fields: CameraSectionField[]
}

const B = (key: string): CameraSectionField => ({ key, group: 'base' })
const A = (key: string): CameraSectionField => ({ key, group: 'advanced' })
const V = (key: string): CameraSectionField => ({ key, group: 'video' })

export const CAMERA_SECTIONS: CameraSectionDef[] = [
  {
    title: 'Image Tone & Colour',
    fields: [B('brightness'), B('contrast'), B('saturation'), B('sharpness'), B('hue'), B('gamma')],
  },
  {
    title: 'Exposure',
    fields: [B('auto_exposureEx'), B('AE_strategy_mode'), B('max_exposure'), B('exposure_time'), B('blc_level')],
  },
  {
    title: 'White Balance',
    fields: [
      B('auto_awb'), B('awb_auto_mode'),
      B('awb_red'), B('awb_green'), B('awb_blue'),
      B('awb_style_red'), B('awb_style_green'), B('awb_style_blue'),
    ],
  },
  {
    title: 'Gain / ISO',
    fields: [
      B('auto_gain_mode'), B('auto_AGain_max'), B('auto_DGain_max'), B('max_sys_gain'),
      B('manual_AGain_enable'), B('manual_AGain'), B('manual_DGain_enable'), B('manual_DGain'),
    ],
  },
  {
    title: 'Wide Dynamic Range & Highlight',
    fields: [A('wdr_level'), A('wdr_sensor'), A('wdr_level_sensor'), A('hlc_enable')],
  },
  {
    title: 'Noise Reduction & Enhancement',
    fields: [A('noiseReduction'), A('_2DNR_level'), A('lens_correction'), B('antiFog'), B('frameTurbo_pro')],
  },
  {
    title: 'Shutter, Flicker & Video Standard',
    fields: [A('low_farme_rate'), A('anti_flicker'), A('power_freq')],
  },
  {
    title: 'Orientation',
    fields: [B('rotate'), A('mirror'), A('flip')],
  },
  {
    title: 'Video Encoding',
    fields: [V('gop')],
  },
]

/** Set of "group:key" covered by CAMERA_SECTIONS, for the "Other" bucket. */
export const CAMERA_SECTION_KEYS: ReadonlySet<string> = new Set(
  CAMERA_SECTIONS.flatMap(s => s.fields.map(f => `${f.group}:${f.key}`)),
)
