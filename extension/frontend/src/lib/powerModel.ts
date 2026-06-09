/**
 * Shared DORIS power model (frontend mirror of backend
 * services/power_model.py). Single source of truth for the power budget
 * used by HomeScreen and MissionProgramming so the planners and the live
 * battery estimator never disagree.
 *
 * Constants were validated against a real 140 min / 791 m dive log
 * (recorder_20260528_165058.mcap):
 *  - LED: measured pack-current rise at 1700 us = 1.28 A vs bench 1.24 A.
 *  - Hotel (Pi5 + autopilot + sensors, lights off): ~8 W.
 *  - Camera recording adder: ~1.5 W.
 *  - Release/burn-wire relay: no measurable draw.
 */

// ── Dive profile ─────────────────────────────────────────────────────
export const DESCENT_RATE_M_S = 1.0
export const ASCENT_BURN_MINUTES = 45.0

// ── Battery pack: 2× Blue Robotics 10 Ah 4S Li-ion packs in parallel ─
export const BATTERY_PACK_COUNT = 2
export const BATTERY_PACK_AH = 10.0
export const BATTERY_NOMINAL_V = 14.8
export const BATTERY_TOTAL_AH = BATTERY_PACK_COUNT * BATTERY_PACK_AH // 20 Ah
export const BATTERY_CAPACITY_WH = BATTERY_TOTAL_AH * BATTERY_NOMINAL_V // 296 Wh
export const BATTERY_RESERVE_PCT = 15.0

// ── Component loads (empirical) ──────────────────────────────────────
export const HOTEL_W = 8.0
export const CAMERA_RECORDING_W = 1.5
export const RELEASE_W = 0.0
export const TYPICAL_DIVE_LOAD_W = 11.0

// ── LED (single) ─────────────────────────────────────────────────────
export const LIGHT_PWM_MIN = 1100
export const LIGHT_PWM_MAX = 1900

// Bench-measured LED current (A) vs PWM (us) at 15 V.
const LED_CURVE: ReadonlyArray<readonly [number, number]> = [
  [1100, 0.01],
  [1200, 0.25],
  [1500, 0.85],
  [1700, 1.24],
  [1900, 1.64],
]

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

export function brightnessToPwm(brightnessPct: number): number {
  const pct = clamp(brightnessPct, 0, 100)
  return LIGHT_PWM_MIN + (pct / 100) * (LIGHT_PWM_MAX - LIGHT_PWM_MIN)
}

export function ledCurrentAtPwm(pwm: number): number {
  if (pwm <= LED_CURVE[0][0]) return LED_CURVE[0][1]
  if (pwm >= LED_CURVE[LED_CURVE.length - 1][0]) return LED_CURVE[LED_CURVE.length - 1][1]
  for (let i = 0; i < LED_CURVE.length - 1; i++) {
    const [pLo, iLo] = LED_CURVE[i]
    const [pHi, iHi] = LED_CURVE[i + 1]
    if (pwm >= pLo && pwm <= pHi) {
      return iLo + ((iHi - iLo) * (pwm - pLo)) / (pHi - pLo)
    }
  }
  return LED_CURVE[LED_CURVE.length - 1][1]
}

/** Power (W) drawn by the single LED at a given brightness (50% < 75%). */
export function ledPowerW(brightnessPct: number, voltage = BATTERY_NOMINAL_V): number {
  return ledCurrentAtPwm(brightnessToPwm(brightnessPct)) * voltage
}

// ── Duty cycles ──────────────────────────────────────────────────────
export function intervalDuty(onSeconds: number, offSeconds: number): number {
  const total = onSeconds + offSeconds
  if (total <= 0) return 1
  return clamp(onSeconds / total, 0, 1)
}

export type LightMode = 'continuous' | 'interval'
export type CameraType = 'continuous-video' | 'video-interval' | 'timelapse'

export function lightDuty(opts: {
  enabled: boolean
  mode?: LightMode
  onSeconds?: number
  offSeconds?: number
}): number {
  if (!opts.enabled) return 0
  if (opts.mode === 'interval') return intervalDuty(opts.onSeconds ?? 0, opts.offSeconds ?? 0)
  return 1
}

export function cameraDuty(opts: {
  enabled: boolean
  mode?: CameraType
  recordSeconds?: number
  pauseSeconds?: number
  capturePeriodSeconds?: number
  snapshotSeconds?: number
}): number {
  if (!opts.enabled) return 0
  if (opts.mode === 'video-interval') {
    return intervalDuty(opts.recordSeconds ?? 0, opts.pauseSeconds ?? 0)
  }
  if (opts.mode === 'timelapse') {
    const period = opts.capturePeriodSeconds ?? 0
    if (period <= 0) return 0
    return clamp((opts.snapshotSeconds ?? 3) / period, 0, 1)
  }
  return 1
}

// ── Per-phase planning ───────────────────────────────────────────────
export interface PhaseConfig {
  lightOn?: boolean
  brightnessPct?: number
  lightMode?: LightMode
  lightOnS?: number
  lightOffS?: number
  cameraOn?: boolean
  cameraType?: CameraType
  recordS?: number
  pauseS?: number
  capturePeriodS?: number
}

export function phaseAveragePowerW(opts: {
  brightnessPct?: number
  lightDutyFraction?: number
  cameraDutyFraction?: number
  voltage?: number
}): number {
  const v = opts.voltage ?? BATTERY_NOMINAL_V
  const led = ledPowerW(opts.brightnessPct ?? 0, v) * clamp(opts.lightDutyFraction ?? 0, 0, 1)
  const cam = CAMERA_RECORDING_W * clamp(opts.cameraDutyFraction ?? 0, 0, 1)
  return HOTEL_W + led + cam
}

export function phasePower(cfg: PhaseConfig, voltage = BATTERY_NOMINAL_V): number {
  const ld = lightDuty({
    enabled: !!cfg.lightOn,
    mode: cfg.lightMode,
    onSeconds: cfg.lightOnS,
    offSeconds: cfg.lightOffS,
  })
  const cd = cameraDuty({
    enabled: !!cfg.cameraOn,
    mode: cfg.cameraType,
    recordSeconds: cfg.recordS,
    pauseSeconds: cfg.pauseS,
    capturePeriodSeconds: cfg.capturePeriodS,
  })
  return phaseAveragePowerW({
    brightnessPct: cfg.brightnessPct,
    lightDutyFraction: ld,
    cameraDutyFraction: cd,
    voltage,
  })
}

export function usableCapacityWh(reservePct = BATTERY_RESERVE_PCT): number {
  return BATTERY_CAPACITY_WH * (1 - clamp(reservePct, 0, 100) / 100)
}

/** Hours until the reserve is hit at a given SOC (%) and average load (W). */
export function hoursRemainingFromSoc(socPct: number, averagePowerW: number): number {
  if (averagePowerW <= 0) return 0
  const usablePct = Math.max(0, socPct - BATTERY_RESERVE_PCT)
  const usableWh = BATTERY_CAPACITY_WH * (usablePct / 100)
  return usableWh / averagePowerW
}

// ── Mapping from saved DeploymentConfiguration shapes ────────────────
interface TimeValueLike {
  number: string | number
  unit: string
}
interface LightSettingsLike {
  enabled: boolean
  mode: LightMode
  brightness: number
  on_time?: TimeValueLike
  off_time?: TimeValueLike
}
interface CameraSettingsLike {
  enabled: boolean
  camera_type: CameraType
  capture_frequency: number
  capture_frequency_unit: string
  video_record?: TimeValueLike
  video_pause?: TimeValueLike
}

export function timeValueToSeconds(tv?: TimeValueLike | null): number {
  if (!tv) return 0
  const n = Number(tv.number) || 0
  if (tv.unit === 'hours') return n * 3600
  if (tv.unit === 'minutes') return n * 60
  return n
}

/** Build a PhaseConfig from saved light/camera settings objects. */
export function phaseConfigFromSettings(
  light?: LightSettingsLike,
  camera?: CameraSettingsLike,
): PhaseConfig {
  return {
    lightOn: !!light?.enabled,
    brightnessPct: light?.brightness ?? 0,
    lightMode: light?.mode ?? 'continuous',
    lightOnS: timeValueToSeconds(light?.on_time),
    lightOffS: timeValueToSeconds(light?.off_time),
    cameraOn: !!camera?.enabled,
    cameraType: camera?.camera_type ?? 'continuous-video',
    recordS: timeValueToSeconds(camera?.video_record),
    pauseS: timeValueToSeconds(camera?.video_pause),
    capturePeriodS: camera
      ? timeValueToSeconds({
          number: camera.capture_frequency,
          unit: camera.capture_frequency_unit,
        })
      : 0,
  }
}

export interface DiveEstimate {
  descentHours: number
  bottomHours: number
  ascentHours: number
  totalHours: number
  descentPowerW: number
  bottomPowerW: number
  ascentPowerW: number
  averagePowerW: number
  energyWh: number
  usagePercent: number
  remainingPercent: number
  batteryLifeHours: number
  fitsWithinReserve: boolean
}

/**
 * Estimate energy/time/battery usage for a planned dive.
 * descent = depth / 1 m/s; bottom = operator release-weight time;
 * ascent = 45 min burn + depth / 1 m/s (ascent settings apply throughout).
 */
export function estimateDive(opts: {
  depthM: number
  bottomTimeHours: number
  descent: PhaseConfig
  bottom: PhaseConfig
  ascent: PhaseConfig
  voltage?: number
  reservePct?: number
}): DiveEstimate {
  const v = opts.voltage ?? BATTERY_NOMINAL_V
  const riseH = Math.max(0, opts.depthM) / DESCENT_RATE_M_S / 3600
  const descentHours = riseH
  const ascentHours = ASCENT_BURN_MINUTES / 60 + riseH
  const bottomHours = Math.max(0, opts.bottomTimeHours)

  const descentPowerW = phasePower(opts.descent, v)
  const bottomPowerW = phasePower(opts.bottom, v)
  const ascentPowerW = phasePower(opts.ascent, v)

  const energyWh =
    descentPowerW * descentHours + bottomPowerW * bottomHours + ascentPowerW * ascentHours
  const totalHours = descentHours + bottomHours + ascentHours
  const averagePowerW = totalHours > 0 ? energyWh / totalHours : 0
  const usagePercent = BATTERY_CAPACITY_WH ? (energyWh / BATTERY_CAPACITY_WH) * 100 : 0
  const batteryLifeHours = averagePowerW > 0 ? BATTERY_CAPACITY_WH / averagePowerW : 0

  return {
    descentHours,
    bottomHours,
    ascentHours,
    totalHours,
    descentPowerW,
    bottomPowerW,
    ascentPowerW,
    averagePowerW,
    energyWh,
    usagePercent: Math.min(usagePercent, 100),
    remainingPercent: Math.max(0, 100 - usagePercent),
    batteryLifeHours,
    fitsWithinReserve: energyWh <= usableCapacityWh(opts.reservePct),
  }
}
