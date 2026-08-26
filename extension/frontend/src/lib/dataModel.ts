/**
 * DORIS on-board data-usage model.  Estimates how many bytes a planned dive
 * will write to disk based on the global video-encoder settings (bitrate,
 * resolution) and the per-phase camera configuration (continuous video,
 * interval video, or timelapse stills).
 *
 * Sibling of powerModel.ts: it reuses the same phase-duration assumptions so
 * the storage and battery planners never disagree about how long each phase
 * lasts.
 *
 *   descent = depth / 1 m/s
 *   bottom  = operator release-weight time
 *   ascent  = 45 min burn + depth / 1 m/s
 *
 * Video storage is bitrate-driven (codec-agnostic: at a fixed bitrate H.264
 * and H.265 write the same number of bytes — H.265 just looks better for it),
 * so only the bitrate and the recording duty cycle matter.  Timelapse storage
 * is driven by the still count and an estimated JPEG size derived from the
 * capture resolution.
 */

import {
  DESCENT_RATE_M_S,
  ASCENT_BURN_MINUTES,
  effectiveCameraDuty,
  type PhaseConfig,
} from './powerModel'

// The camera-settings UI expresses bitrate in kbps where 1 Mbps = 1024 kbps
// (see bitrateOptions in MissionProgramming.vue).  Keep the same 1024 base
// throughout so byte counts line up with the labels the operator picks.
const BITS_PER_KBIT = 1024
const BITS_PER_BYTE = 8

// Estimated compressed size of one timelapse still, per pixel.  RadCam stills
// are high-quality 4:2:0 JPEGs; ~0.5 bytes/pixel is a good real-world average
// (a 4K frame lands around 4 MB).  Scenes with more detail run larger.
export const STILL_BYTES_PER_PIXEL = 0.5

// Fallback capture resolution when the encoder width/height are unknown.
const DEFAULT_STILL_WIDTH = 3840
const DEFAULT_STILL_HEIGHT = 2160

/** Bytes written by continuous/interval video at a bitrate over a duration. */
export function videoBytes(bitrateKbps: number, seconds: number): number {
  if (bitrateKbps <= 0 || seconds <= 0) return 0
  return (bitrateKbps * BITS_PER_KBIT * seconds) / BITS_PER_BYTE
}

/** Estimated bytes for a single timelapse still at a given resolution. */
export function stillBytes(widthPx?: number, heightPx?: number): number {
  const w = widthPx && widthPx > 0 ? widthPx : DEFAULT_STILL_WIDTH
  const h = heightPx && heightPx > 0 ? heightPx : DEFAULT_STILL_HEIGHT
  return w * h * STILL_BYTES_PER_PIXEL
}

export interface DataPhaseBreakdown {
  name: string
  hours: number
  /** 'continuous-video' | 'video-interval' | 'timelapse' | 'off' */
  mode: string
  /** Effective recording duty (0–1) for video modes. */
  cameraDuty: number
  /** Seconds of actual video written (video modes only). */
  recordSeconds: number
  /** Number of stills written (timelapse only). */
  stillCount: number
  bytes: number
}

/** Per-phase storage for a phase of a given wall-clock duration. */
export function dataPhaseBreakdown(
  name: string,
  cfg: PhaseConfig,
  hours: number,
  opts: { bitrateKbps: number; stillWidth?: number; stillHeight?: number },
): DataPhaseBreakdown {
  const seconds = Math.max(0, hours) * 3600

  if (!cfg.cameraOn || seconds <= 0) {
    return {
      name,
      hours,
      mode: 'off',
      cameraDuty: 0,
      recordSeconds: 0,
      stillCount: 0,
      bytes: 0,
    }
  }

  if (cfg.cameraType === 'timelapse') {
    const period = cfg.capturePeriodS ?? 0
    const stillCount = period > 0 ? Math.floor(seconds / period) : 0
    const bytes = stillCount * stillBytes(opts.stillWidth, opts.stillHeight)
    return {
      name,
      hours,
      mode: 'timelapse',
      cameraDuty: 0,
      recordSeconds: 0,
      stillCount,
      bytes,
    }
  }

  // continuous-video and video-interval are both bitrate-driven; the duty
  // cycle handles the interval on/off ratio (continuous returns 1.0).
  const duty = effectiveCameraDuty(cfg)
  const recordSeconds = seconds * duty
  return {
    name,
    hours,
    mode: cfg.cameraType ?? 'continuous-video',
    cameraDuty: duty,
    recordSeconds,
    stillCount: 0,
    bytes: videoBytes(opts.bitrateKbps, recordSeconds),
  }
}

export interface DataEstimate {
  descentHours: number
  bottomHours: number
  ascentHours: number
  totalHours: number
  bitrateKbps: number
  phases: DataPhaseBreakdown[]
  totalBytes: number
}

/**
 * Estimate total on-board storage for a planned dive.  Mirrors estimateDive()
 * in powerModel.ts for phase durations so the two planners agree.
 */
export function estimateDataUsage(opts: {
  depthM: number
  bottomTimeHours: number
  descent: PhaseConfig
  bottom: PhaseConfig
  ascent: PhaseConfig
  bitrateKbps: number
  stillWidth?: number
  stillHeight?: number
}): DataEstimate {
  const riseH = Math.max(0, opts.depthM) / DESCENT_RATE_M_S / 3600
  const descentHours = riseH
  const ascentHours = ASCENT_BURN_MINUTES / 60 + riseH
  const bottomHours = Math.max(0, opts.bottomTimeHours)

  const common = {
    bitrateKbps: opts.bitrateKbps,
    stillWidth: opts.stillWidth,
    stillHeight: opts.stillHeight,
  }
  const phases = [
    dataPhaseBreakdown('Descent', opts.descent, descentHours, common),
    dataPhaseBreakdown('On Bottom', opts.bottom, bottomHours, common),
    dataPhaseBreakdown('Ascent', opts.ascent, ascentHours, common),
  ]
  const totalBytes = phases.reduce((s, p) => s + p.bytes, 0)

  return {
    descentHours,
    bottomHours,
    ascentHours,
    totalHours: descentHours + bottomHours + ascentHours,
    bitrateKbps: opts.bitrateKbps,
    phases,
    totalBytes,
  }
}

/** Human-readable byte size using 1024 steps (matches the bitrate convention). */
export function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 MB'
  const mb = bytes / 1024 / 1024
  if (mb < 1) return `${(bytes / 1024).toFixed(0)} KB`
  if (mb < 1024) return `${mb.toFixed(mb < 10 ? 1 : 0)} MB`
  const gb = mb / 1024
  if (gb < 1024) return `${gb.toFixed(gb < 10 ? 2 : 1)} GB`
  const tb = gb / 1024
  return `${tb.toFixed(2)} TB`
}
