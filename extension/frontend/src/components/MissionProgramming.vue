<script setup lang="ts">
import { ref, watch, nextTick, onMounted, onUnmounted, computed } from 'vue'
import { Settings, Save, Copy, AlertTriangle, ChevronDown, ChevronUp, Camera as CameraIcon, Lightbulb, Database as DatabaseIcon, Battery, ArrowDown, Anchor, ArrowUp, Radio, X, Trash2 } from 'lucide-vue-next'
import type { Screen } from '../types'
import { useConfigurations, useCameraSettings, usePresets } from '../composables/useApi'
import type {
  DeploymentConfiguration,
  CameraSettingsBundle,
  CameraBaseSettings,
  CameraAdvancedSettings,
} from '../composables/useApi'
import {
  estimateDive,
  BASE_W,
  CAMERA_RECORDING_W,
  BATTERY_CAPACITY_WH,
  ASCENT_BURN_MINUTES,
  type PhaseConfig,
  type CameraType,
  type LightMode,
} from '../lib/powerModel'
import { cameraFieldMeta, CAMERA_SECTIONS, CAMERA_SECTION_KEYS } from '../lib/cameraSettingsSchema'
import { estimateDataUsage, formatBytes, STILL_BYTES_PER_PIXEL } from '../lib/dataModel'

const POWER = { BASE_W, CAMERA_RECORDING_W, BATTERY_CAPACITY_WH, ASCENT_BURN_MINUTES }

const props = withDefaults(defineProps<{
  releaseWeightBy: 'datetime' | 'elapsed'
  initialConfiguration?: string
}>(), {
  initialConfiguration: ''
})

const emit = defineEmits<{
  navigate: [screen: Screen]
  'update:releaseWeightBy': [value: 'datetime' | 'elapsed']
}>()

const diveName = ref('Dive II')
const selectedConfiguration = ref(props.initialConfiguration || '')
const estimatedDepth = ref('')
const warnings = ref<string[]>([])
const showBatteryPlanning = ref(false)
const showBatteryBreakdown = ref(false)
const showDataPlanning = ref(false)
const showDataBreakdown = ref(false)
const showSaveModal = ref(false)
const configurationName = ref('')
const showDeleteModal = ref(false)
const deleting = ref(false)
const deleteError = ref('')
const hasUnsavedChanges = ref(false)
let suppressUnsavedTracking = false
const showNavigationWarning = ref(false)
const pendingConfigurationChange = ref('')
const {
  configurations: savedConfigSummaries,
  fetchConfigurations,
  loadConfiguration,
  saveConfiguration,
  deleteConfiguration,
  error: configurationSaveError,
  clearError: clearConfigurationSaveError,
} = useConfigurations()

const savedConfigurations = computed(() => savedConfigSummaries.value.map(c => c.name))

const showBrightnessWarning = ref(false)
const pendingBrightness = ref<{ value: number; phase: 'descent' | 'bottom' | 'ascent' } | null>(null)

// Descent settings
const descentCameraOn = ref(false)
const descentCameraType = ref<'continuous-video' | 'timelapse' | 'video-interval'>('continuous-video')
const descentVideoRecordNumber = ref('10')
const descentVideoRecordUnit = ref('seconds')
const descentVideoPauseNumber = ref('5')
const descentVideoPauseUnit = ref('seconds')
const descentCaptureFrequency = ref(10)
const descentCaptureFrequencyUnit = ref('seconds')
const descentSleepTimerNumber = ref('')
const descentSleepTimerUnit = ref('hours')
const descentSleepTimerEnabled = ref(false)
const descentLightOn = ref(false)
const descentLightMode = ref<'continuous' | 'interval'>('continuous')
const descentLightOnNumber = ref('10')
const descentLightOnUnit = ref('seconds')
const descentLightOffNumber = ref('5')
const descentLightOffUnit = ref('seconds')
const descentLightBrightness = ref(60)
const descentAutoWhiteBalance = ref(false)
const descentMatchCameraInterval = ref(false)

// On Bottom settings
const bottomCameraOn = ref(true)
const bottomCameraDelayNumber = ref('30')
const bottomCameraDelayUnit = ref('seconds')
const bottomCameraType = ref<'continuous-video' | 'timelapse' | 'video-interval'>('continuous-video')
const bottomVideoRecordNumber = ref('10')
const bottomVideoRecordUnit = ref('seconds')
const bottomVideoPauseNumber = ref('5')
const bottomVideoPauseUnit = ref('seconds')
const bottomCaptureFrequency = ref(10)
const bottomCaptureFrequencyUnit = ref('seconds')
const bottomTimelapseLightPreNumber = ref('2')
const bottomTimelapseLightPostNumber = ref('1')
const bottomSleepTimerNumber = ref('')
const bottomSleepTimerUnit = ref('hours')
const bottomSleepTimerEnabled = ref(false)
const bottomLightOn = ref(true)
const bottomLightDelayNumber = ref('30')
const bottomLightDelayUnit = ref('seconds')
const bottomLightMode = ref<'continuous' | 'interval'>('continuous')
const bottomLightOnNumber = ref('10')
const bottomLightOnUnit = ref('seconds')
const bottomLightOffNumber = ref('5')
const bottomLightOffUnit = ref('seconds')
const bottomLightBrightness = ref(60)
const bottomAutoWhiteBalance = ref(false)
const bottomMatchCameraInterval = ref(false)

// Ascent settings
const ascentSameAsDescent = ref(false)
const releaseWeightDate = ref('2026-02-02')
const releaseWeightTime = ref('12:00')
const releaseWeightElapsedNumber = ref('6')
const releaseWeightElapsedUnit = ref('hours')
const ascentCameraOn = ref(false)
const ascentCameraType = ref<'continuous-video' | 'timelapse' | 'video-interval'>('continuous-video')
const ascentVideoRecordNumber = ref('10')
const ascentVideoRecordUnit = ref('seconds')
const ascentVideoPauseNumber = ref('5')
const ascentVideoPauseUnit = ref('seconds')
const ascentCaptureFrequency = ref(10)
const ascentCaptureFrequencyUnit = ref('seconds')
const ascentSleepTimerNumber = ref('')
const ascentSleepTimerUnit = ref('hours')
const ascentSleepTimerEnabled = ref(false)
const ascentLightOn = ref(false)
const ascentLightMode = ref<'continuous' | 'interval'>('continuous')
const ascentLightOnNumber = ref('10')
const ascentLightOnUnit = ref('seconds')
const ascentLightOffNumber = ref('5')
const ascentLightOffUnit = ref('seconds')
const ascentLightBrightness = ref(60)
const ascentAutoWhiteBalance = ref(false)
const ascentMatchCameraInterval = ref(false)

// Recovery settings
const activateMastLight = ref(false)
const updateFrequency = ref('5min')
const useIridium = ref(false)
const useLoRA = ref(false)

// Convert a {number, unit} pair from the planner UI into seconds.
function toSeconds(value: string | number, unit: string): number {
  const n = Number(value) || 0
  if (unit === 'hours') return n * 3600
  if (unit === 'minutes') return n * 60
  return n
}

// Bottom time (hours) from the operator's release-weight setting.
const bottomTimeHours = computed(() => {
  if (props.releaseWeightBy === 'elapsed') {
    return toSeconds(releaseWeightElapsedNumber.value, releaseWeightElapsedUnit.value) / 3600
  }
  if (releaseWeightDate.value && releaseWeightTime.value) {
    const release = new Date(`${releaseWeightDate.value}T${releaseWeightTime.value}:00Z`)
    const hrs = (release.getTime() - Date.now()) / 3_600_000
    return Math.max(0, hrs)
  }
  return 0
})

const descentPhase = computed<PhaseConfig>(() => ({
  lightOn: descentLightOn.value,
  brightnessPct: descentLightBrightness.value,
  lightMode: descentLightMode.value as LightMode,
  lightOnS: toSeconds(descentLightOnNumber.value, descentLightOnUnit.value),
  lightOffS: toSeconds(descentLightOffNumber.value, descentLightOffUnit.value),
  cameraOn: descentCameraOn.value,
  cameraType: descentCameraType.value as CameraType,
  recordS: toSeconds(descentVideoRecordNumber.value, descentVideoRecordUnit.value),
  pauseS: toSeconds(descentVideoPauseNumber.value, descentVideoPauseUnit.value),
  capturePeriodS: toSeconds(descentCaptureFrequency.value, descentCaptureFrequencyUnit.value),
}))

const bottomPhase = computed<PhaseConfig>(() => ({
  lightOn: bottomLightOn.value,
  brightnessPct: bottomLightBrightness.value,
  lightMode: bottomLightMode.value as LightMode,
  lightOnS: toSeconds(bottomLightOnNumber.value, bottomLightOnUnit.value),
  lightOffS: toSeconds(bottomLightOffNumber.value, bottomLightOffUnit.value),
  cameraOn: bottomCameraOn.value,
  cameraType: bottomCameraType.value as CameraType,
  recordS: toSeconds(bottomVideoRecordNumber.value, bottomVideoRecordUnit.value),
  pauseS: toSeconds(bottomVideoPauseNumber.value, bottomVideoPauseUnit.value),
  capturePeriodS: toSeconds(bottomCaptureFrequency.value, bottomCaptureFrequencyUnit.value),
  timelapsePreS: toSeconds(bottomTimelapseLightPreNumber.value, 'seconds'),
  timelapsePostS: toSeconds(bottomTimelapseLightPostNumber.value, 'seconds'),
}))

const ascentPhase = computed<PhaseConfig>(() => ({
  lightOn: ascentLightOn.value,
  brightnessPct: ascentLightBrightness.value,
  lightMode: ascentLightMode.value as LightMode,
  lightOnS: toSeconds(ascentLightOnNumber.value, ascentLightOnUnit.value),
  lightOffS: toSeconds(ascentLightOffNumber.value, ascentLightOffUnit.value),
  cameraOn: ascentCameraOn.value,
  cameraType: ascentCameraType.value as CameraType,
  recordS: toSeconds(ascentVideoRecordNumber.value, ascentVideoRecordUnit.value),
  pauseS: toSeconds(ascentVideoPauseNumber.value, ascentVideoPauseUnit.value),
  capturePeriodS: toSeconds(ascentCaptureFrequency.value, ascentCaptureFrequencyUnit.value),
}))

const batteryData = computed(() => {
  const estimate = estimateDive({
    depthM: parseFloat(estimatedDepth.value) || 0,
    bottomTimeHours: bottomTimeHours.value,
    descent: descentPhase.value,
    bottom: bottomPhase.value,
    ascent: ascentPhase.value,
  })
  return {
    totalPower: estimate.averagePowerW,
    energyWh: estimate.energyWh,
    batteryLife: estimate.batteryLifeHours,
    batteryUsagePercent: estimate.usagePercent,
    diveDuration: estimate.totalHours,
    estimate,
  }
})

// Estimated on-board storage for the planned dive, driven by the global video
// bitrate/resolution and the per-phase camera mode (see lib/dataModel.ts).
const dataUsage = computed(() => {
  const bitrateKbps = Number(videoForm.value.bitrate) || 0
  const estimate = estimateDataUsage({
    depthM: parseFloat(estimatedDepth.value) || 0,
    bottomTimeHours: bottomTimeHours.value,
    descent: descentPhase.value,
    bottom: bottomPhase.value,
    ascent: ascentPhase.value,
    bitrateKbps,
    stillWidth: Number(videoForm.value.pic_width) || undefined,
    stillHeight: Number(videoForm.value.pic_height) || undefined,
  })
  return {
    estimate,
    totalLabel: formatBytes(estimate.totalBytes),
    bitrateMbps: bitrateKbps / 1024,
  }
})

// Bottom timelapse strobe helpers.  Both pre/post are stored in
// seconds (matching DORIS_TL_PRE_S / DORIS_TL_PST_S on the
// autopilot); ``minSeconds`` reflects the Lua's ``pre + post + 1``
// cycle floor so the UI can warn the operator when they pick a
// capture frequency tighter than the strobe schedule needs.
const bottomCaptureFrequencySeconds = computed(() => {
  const n = Number(bottomCaptureFrequency.value) || 0
  if (bottomCaptureFrequencyUnit.value === 'hours') return n * 3600
  if (bottomCaptureFrequencyUnit.value === 'minutes') return n * 60
  return n
})
const bottomTimelapseMinSeconds = computed(() => {
  const pre = Math.max(0, Number(bottomTimelapseLightPreNumber.value) || 0)
  const post = Math.max(0, Number(bottomTimelapseLightPostNumber.value) || 0)
  return Math.max(1, pre + post)
})
const bottomTimelapseTooFast = computed(() =>
  bottomCameraType.value === 'timelapse'
  && bottomCaptureFrequencySeconds.value < bottomTimelapseMinSeconds.value,
)

const descentCaptureFrequencyTooLow = computed(() => {
  const totalHours = descentCaptureFrequencyUnit.value === 'hours'
    ? descentCaptureFrequency.value
    : descentCaptureFrequencyUnit.value === 'minutes'
    ? descentCaptureFrequency.value / 60
    : descentCaptureFrequency.value / 3600
  return totalHours > 1
})

const releaseWeightWarning = computed(() => {
  // This value is the bottom time before weight release (counted from when
  // DORIS reaches the seafloor), not total dive duration. A short bottom
  // time is a valid choice, so we only flag implausibly long values.
  const totalMinutes = releaseWeightElapsedUnit.value === 'hours'
    ? Number(releaseWeightElapsedNumber.value) * 60
    : releaseWeightElapsedUnit.value === 'minutes'
    ? Number(releaseWeightElapsedNumber.value)
    : Number(releaseWeightElapsedNumber.value) / 60
  if (totalMinutes > 1200) return { show: true, severity: 'error' as const, title: 'Bottom Time Too Long', message: 'Bottom time before release exceeds 20 hours. Consider if this extended duration is necessary for mission objectives.' }
  return { show: false, severity: 'warning' as const, title: '', message: '' }
})

function isDelayTooLong(number: string, unit: string): boolean {
  const totalHours = unit === 'hours' ? Number(number) : unit === 'minutes' ? Number(number) / 60 : Number(number) / 3600
  return totalHours > 4
}

function isRecordTooLong(number: string, unit: string): boolean {
  const totalHours = unit === 'hours' ? Number(number) : unit === 'minutes' ? Number(number) / 60 : Number(number) / 3600
  return totalHours > 4
}

watch(() => props.initialConfiguration, (val) => {
  if (val) selectedConfiguration.value = val
})

watch([diveName, descentCameraOn, descentCameraType, descentCaptureFrequency,
  descentLightOn, descentLightMode, descentLightBrightness, descentAutoWhiteBalance, bottomCameraOn, bottomCameraType,
  bottomCaptureFrequency, bottomLightOn, bottomLightMode, bottomLightBrightness, bottomAutoWhiteBalance, ascentCameraOn, ascentCameraType,
  ascentCaptureFrequency, ascentLightOn, ascentLightMode, ascentLightBrightness, ascentAutoWhiteBalance,
  activateMastLight, updateFrequency, useIridium, useLoRA, releaseWeightElapsedNumber
], () => {
  if (suppressUnsavedTracking) return
  if (selectedConfiguration.value) {
    hasUnsavedChanges.value = true
  }
})

function resetToDefaults() {
  suppressUnsavedTracking = true
  diveName.value = 'Dive II'
  estimatedDepth.value = ''
  selectedConfiguration.value = 'New Configuration'
  warnings.value = []
  hasUnsavedChanges.value = false
  descentCameraOn.value = false
  descentCameraType.value = 'continuous-video'
  descentVideoRecordNumber.value = '10'
  descentVideoRecordUnit.value = 'seconds'
  descentVideoPauseNumber.value = '5'
  descentVideoPauseUnit.value = 'seconds'
  descentCaptureFrequency.value = 10
  descentCaptureFrequencyUnit.value = 'seconds'
  descentSleepTimerEnabled.value = false
  descentSleepTimerNumber.value = ''
  descentSleepTimerUnit.value = 'hours'
  descentLightOn.value = false
  descentLightMode.value = 'continuous'
  descentLightOnNumber.value = '10'
  descentLightOnUnit.value = 'seconds'
  descentLightOffNumber.value = '5'
  descentLightOffUnit.value = 'seconds'
  descentLightBrightness.value = 60
  descentAutoWhiteBalance.value = false
  descentMatchCameraInterval.value = false
  bottomCameraOn.value = true
  bottomCameraDelayNumber.value = '30'
  bottomCameraDelayUnit.value = 'seconds'
  bottomCameraType.value = 'continuous-video'
  bottomVideoRecordNumber.value = '10'
  bottomVideoRecordUnit.value = 'seconds'
  bottomVideoPauseNumber.value = '5'
  bottomVideoPauseUnit.value = 'seconds'
  bottomCaptureFrequency.value = 10
  bottomCaptureFrequencyUnit.value = 'seconds'
  bottomTimelapseLightPreNumber.value = '2'
  bottomTimelapseLightPostNumber.value = '1'
  bottomSleepTimerEnabled.value = false
  bottomSleepTimerNumber.value = ''
  bottomSleepTimerUnit.value = 'hours'
  bottomLightOn.value = true
  bottomLightDelayNumber.value = '30'
  bottomLightDelayUnit.value = 'seconds'
  bottomLightMode.value = 'continuous'
  bottomLightOnNumber.value = '10'
  bottomLightOnUnit.value = 'seconds'
  bottomLightOffNumber.value = '5'
  bottomLightOffUnit.value = 'seconds'
  bottomLightBrightness.value = 60
  bottomAutoWhiteBalance.value = false
  bottomMatchCameraInterval.value = false
  ascentSameAsDescent.value = false
  emit('update:releaseWeightBy', 'elapsed')
  releaseWeightDate.value = '2026-02-02'
  releaseWeightTime.value = '12:00'
  releaseWeightElapsedNumber.value = '6'
  releaseWeightElapsedUnit.value = 'hours'
  ascentCameraOn.value = false
  ascentCameraType.value = 'continuous-video'
  ascentVideoRecordNumber.value = '10'
  ascentVideoRecordUnit.value = 'seconds'
  ascentVideoPauseNumber.value = '5'
  ascentVideoPauseUnit.value = 'seconds'
  ascentCaptureFrequency.value = 10
  ascentCaptureFrequencyUnit.value = 'seconds'
  ascentSleepTimerEnabled.value = false
  ascentSleepTimerNumber.value = ''
  ascentSleepTimerUnit.value = 'hours'
  ascentLightOn.value = false
  ascentLightMode.value = 'continuous'
  ascentLightOnNumber.value = '10'
  ascentLightOnUnit.value = 'seconds'
  ascentLightOffNumber.value = '5'
  ascentLightOffUnit.value = 'seconds'
  ascentLightBrightness.value = 60
  ascentAutoWhiteBalance.value = false
  ascentMatchCameraInterval.value = false
  activateMastLight.value = false
  updateFrequency.value = '5min'
  useIridium.value = false
  useLoRA.value = false
  nextTick(() => { suppressUnsavedTracking = false })
}

/** v-model.number can be NaN when the field is cleared; JSON would send null and fail API validation. */
function safePositiveInt(value: number, fallback: number): number {
  const n = Number(value)
  return Number.isFinite(n) && n >= 1 ? Math.floor(n) : fallback
}

/** input type="number" coerces refs to JS numbers; backend TimeValue.number expects str. */
function tv(number: string | number, unit: string): { number: string; unit: 'seconds' | 'minutes' | 'hours' } {
  return { number: String(number ?? '0'), unit: unit as 'seconds' | 'minutes' | 'hours' }
}

function buildConfigPayload(name: string): DeploymentConfiguration {
  return {
    name,
    dive_name: diveName.value,
    estimated_depth: estimatedDepth.value,
    descent: {
      camera: {
        enabled: descentCameraOn.value,
        camera_type: descentCameraType.value,
        capture_frequency: safePositiveInt(descentCaptureFrequency.value, 10),
        capture_frequency_unit: descentCaptureFrequencyUnit.value as 'seconds' | 'minutes' | 'hours',
        video_record: tv(descentVideoRecordNumber.value, descentVideoRecordUnit.value),
        video_pause: tv(descentVideoPauseNumber.value, descentVideoPauseUnit.value),
        sleep_timer_enabled: descentSleepTimerEnabled.value,
        sleep_timer: tv(descentSleepTimerNumber.value, descentSleepTimerUnit.value),
      },
      light: {
        enabled: descentLightOn.value,
        mode: descentLightMode.value,
        brightness: descentLightBrightness.value,
        match_camera_interval: descentMatchCameraInterval.value,
        on_time: tv(descentLightOnNumber.value, descentLightOnUnit.value),
        off_time: tv(descentLightOffNumber.value, descentLightOffUnit.value),
      },
      auto_white_balance: descentAutoWhiteBalance.value,
    },
    bottom: {
      camera: {
        enabled: bottomCameraOn.value,
        camera_type: bottomCameraType.value,
        capture_frequency: safePositiveInt(bottomCaptureFrequency.value, 10),
        capture_frequency_unit: bottomCaptureFrequencyUnit.value as 'seconds' | 'minutes' | 'hours',
        video_record: tv(bottomVideoRecordNumber.value, bottomVideoRecordUnit.value),
        video_pause: tv(bottomVideoPauseNumber.value, bottomVideoPauseUnit.value),
        timelapse_light_pre: tv(bottomTimelapseLightPreNumber.value, 'seconds'),
        timelapse_light_post: tv(bottomTimelapseLightPostNumber.value, 'seconds'),
        sleep_timer_enabled: bottomSleepTimerEnabled.value,
        sleep_timer: tv(bottomSleepTimerNumber.value, bottomSleepTimerUnit.value),
      },
      camera_delay: tv(bottomCameraDelayNumber.value, bottomCameraDelayUnit.value),
      light: {
        enabled: bottomLightOn.value,
        mode: bottomLightMode.value,
        brightness: bottomLightBrightness.value,
        match_camera_interval: bottomMatchCameraInterval.value,
        on_time: tv(bottomLightOnNumber.value, bottomLightOnUnit.value),
        off_time: tv(bottomLightOffNumber.value, bottomLightOffUnit.value),
      },
      light_delay: tv(bottomLightDelayNumber.value, bottomLightDelayUnit.value),
      auto_white_balance: bottomAutoWhiteBalance.value,
    },
    ascent: {
      same_as_descent: ascentSameAsDescent.value,
      camera: {
        enabled: ascentCameraOn.value,
        camera_type: ascentCameraType.value,
        capture_frequency: safePositiveInt(ascentCaptureFrequency.value, 10),
        capture_frequency_unit: ascentCaptureFrequencyUnit.value as 'seconds' | 'minutes' | 'hours',
        video_record: tv(ascentVideoRecordNumber.value, ascentVideoRecordUnit.value),
        video_pause: tv(ascentVideoPauseNumber.value, ascentVideoPauseUnit.value),
        sleep_timer_enabled: ascentSleepTimerEnabled.value,
        sleep_timer: tv(ascentSleepTimerNumber.value, ascentSleepTimerUnit.value),
      },
      light: {
        enabled: ascentLightOn.value,
        mode: ascentLightMode.value,
        brightness: ascentLightBrightness.value,
        match_camera_interval: ascentMatchCameraInterval.value,
        on_time: tv(ascentLightOnNumber.value, ascentLightOnUnit.value),
        off_time: tv(ascentLightOffNumber.value, ascentLightOffUnit.value),
      },
      release_weight: {
        method: props.releaseWeightBy,
        elapsed: tv(releaseWeightElapsedNumber.value, releaseWeightElapsedUnit.value),
        release_date: releaseWeightDate.value,
        release_time: releaseWeightTime.value,
      },
      auto_white_balance: ascentAutoWhiteBalance.value,
    },
    recovery: {
      activate_mast_light: activateMastLight.value,
      update_frequency: updateFrequency.value,
      use_iridium: useIridium.value,
      use_lora: useLoRA.value,
    },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  }
}

function applyConfig(cfg: DeploymentConfiguration) {
  suppressUnsavedTracking = true
  diveName.value = cfg.dive_name
  estimatedDepth.value = cfg.estimated_depth

  descentCameraOn.value = cfg.descent.camera.enabled
  // Lua only supports timelapse + video-interval on the bottom phase
  // (services/dive._ipcam_phase_enabled returns 1.0 only for CONTINUOUS_VIDEO).
  // Coerce legacy descent profiles with invalid modes back to continuous-video
  // so the disabled UI radios don't render an unselected/invalid state.
  descentCameraType.value =
    cfg.descent.camera.camera_type === 'timelapse' || cfg.descent.camera.camera_type === 'video-interval'
      ? 'continuous-video'
      : cfg.descent.camera.camera_type
  descentCaptureFrequency.value = cfg.descent.camera.capture_frequency
  descentCaptureFrequencyUnit.value = cfg.descent.camera.capture_frequency_unit
  descentVideoRecordNumber.value = cfg.descent.camera.video_record.number
  descentVideoRecordUnit.value = cfg.descent.camera.video_record.unit
  descentVideoPauseNumber.value = cfg.descent.camera.video_pause.number
  descentVideoPauseUnit.value = cfg.descent.camera.video_pause.unit
  descentSleepTimerEnabled.value = cfg.descent.camera.sleep_timer_enabled
  descentSleepTimerNumber.value = cfg.descent.camera.sleep_timer.number
  descentSleepTimerUnit.value = cfg.descent.camera.sleep_timer.unit
  descentLightOn.value = cfg.descent.light.enabled
  descentLightMode.value = cfg.descent.light.mode
  descentLightBrightness.value = cfg.descent.light.brightness
  descentMatchCameraInterval.value = cfg.descent.light.match_camera_interval
  descentLightOnNumber.value = cfg.descent.light.on_time.number
  descentLightOnUnit.value = cfg.descent.light.on_time.unit
  descentLightOffNumber.value = cfg.descent.light.off_time.number
  descentLightOffUnit.value = cfg.descent.light.off_time.unit
  descentAutoWhiteBalance.value = cfg.descent.auto_white_balance ?? false

  bottomCameraOn.value = cfg.bottom.camera.enabled
  bottomCameraDelayNumber.value = cfg.bottom.camera_delay.number
  bottomCameraDelayUnit.value = cfg.bottom.camera_delay.unit
  bottomCameraType.value = cfg.bottom.camera.camera_type
  bottomCaptureFrequency.value = cfg.bottom.camera.capture_frequency
  bottomCaptureFrequencyUnit.value = cfg.bottom.camera.capture_frequency_unit
  bottomVideoRecordNumber.value = cfg.bottom.camera.video_record.number
  bottomVideoRecordUnit.value = cfg.bottom.camera.video_record.unit
  bottomVideoPauseNumber.value = cfg.bottom.camera.video_pause.number
  bottomVideoPauseUnit.value = cfg.bottom.camera.video_pause.unit
  // Older configurations predate the timelapse light strobe windows;
  // fall back to the (2 s pre, 1 s post) defaults so the form still
  // populates cleanly and the user can immediately tune them.
  bottomTimelapseLightPreNumber.value = cfg.bottom.camera.timelapse_light_pre?.number ?? '2'
  bottomTimelapseLightPostNumber.value = cfg.bottom.camera.timelapse_light_post?.number ?? '1'
  bottomSleepTimerEnabled.value = cfg.bottom.camera.sleep_timer_enabled
  bottomSleepTimerNumber.value = cfg.bottom.camera.sleep_timer.number
  bottomSleepTimerUnit.value = cfg.bottom.camera.sleep_timer.unit
  bottomLightOn.value = cfg.bottom.light.enabled
  bottomLightDelayNumber.value = cfg.bottom.light_delay.number
  bottomLightDelayUnit.value = cfg.bottom.light_delay.unit
  bottomLightMode.value = cfg.bottom.light.mode
  bottomLightBrightness.value = cfg.bottom.light.brightness
  bottomMatchCameraInterval.value = cfg.bottom.light.match_camera_interval
  bottomLightOnNumber.value = cfg.bottom.light.on_time.number
  bottomLightOnUnit.value = cfg.bottom.light.on_time.unit
  bottomLightOffNumber.value = cfg.bottom.light.off_time.number
  bottomLightOffUnit.value = cfg.bottom.light.off_time.unit
  bottomAutoWhiteBalance.value = cfg.bottom.auto_white_balance ?? false

  ascentSameAsDescent.value = cfg.ascent.same_as_descent
  ascentCameraOn.value = cfg.ascent.camera.enabled
  // See descentCameraType comment above: ascent has the same restriction.
  ascentCameraType.value =
    cfg.ascent.camera.camera_type === 'timelapse' || cfg.ascent.camera.camera_type === 'video-interval'
      ? 'continuous-video'
      : cfg.ascent.camera.camera_type
  ascentCaptureFrequency.value = cfg.ascent.camera.capture_frequency
  ascentCaptureFrequencyUnit.value = cfg.ascent.camera.capture_frequency_unit
  ascentVideoRecordNumber.value = cfg.ascent.camera.video_record.number
  ascentVideoRecordUnit.value = cfg.ascent.camera.video_record.unit
  ascentVideoPauseNumber.value = cfg.ascent.camera.video_pause.number
  ascentVideoPauseUnit.value = cfg.ascent.camera.video_pause.unit
  ascentSleepTimerEnabled.value = cfg.ascent.camera.sleep_timer_enabled
  ascentSleepTimerNumber.value = cfg.ascent.camera.sleep_timer.number
  ascentSleepTimerUnit.value = cfg.ascent.camera.sleep_timer.unit
  ascentLightOn.value = cfg.ascent.light.enabled
  ascentLightMode.value = cfg.ascent.light.mode
  ascentLightBrightness.value = cfg.ascent.light.brightness
  ascentMatchCameraInterval.value = cfg.ascent.light.match_camera_interval
  ascentLightOnNumber.value = cfg.ascent.light.on_time.number
  ascentLightOnUnit.value = cfg.ascent.light.on_time.unit
  ascentLightOffNumber.value = cfg.ascent.light.off_time.number
  ascentLightOffUnit.value = cfg.ascent.light.off_time.unit
  ascentAutoWhiteBalance.value = cfg.ascent.auto_white_balance ?? false
  releaseWeightElapsedNumber.value = cfg.ascent.release_weight.elapsed.number
  releaseWeightElapsedUnit.value = cfg.ascent.release_weight.elapsed.unit
  releaseWeightDate.value = cfg.ascent.release_weight.release_date
  releaseWeightTime.value = cfg.ascent.release_weight.release_time
  emit('update:releaseWeightBy', cfg.ascent.release_weight.method)

  activateMastLight.value = cfg.recovery.activate_mast_light
  updateFrequency.value = cfg.recovery.update_frequency
  useIridium.value = cfg.recovery.use_iridium
  useLoRA.value = cfg.recovery.use_lora

  hasUnsavedChanges.value = false
  nextTick(() => { suppressUnsavedTracking = false })
}

function generateNextConfigName(baseName: string): string {
  const match = baseName.match(/^(.*?)(\d+)?$/)
  if (!match) return `${baseName} 2`
  const base = match[1].trim()
  const currentNumber = match[2] ? parseInt(match[2]) : 1
  let checkNumber = currentNumber + 1
  let proposedName = `${base} ${checkNumber}`
  while (savedConfigurations.value.includes(proposedName)) {
    checkNumber++
    proposedName = `${base} ${checkNumber}`
  }
  return proposedName
}

async function handleSaveConfiguration() {
  const name = configurationName.value.trim()
  if (!name) return
  const payload = buildConfigPayload(name)
  const saved = await saveConfiguration(payload)
  if (saved) {
    selectedConfiguration.value = name
    configurationName.value = ''
    showSaveModal.value = false
    hasUnsavedChanges.value = false
    if (pendingConfigurationChange.value) {
      selectedConfiguration.value = pendingConfigurationChange.value
      if (pendingConfigurationChange.value === 'New Configuration') resetToDefaults()
      pendingConfigurationChange.value = ''
      showNavigationWarning.value = false
    }
  }
}

async function handleDiscardChanges() {
  hasUnsavedChanges.value = false
  showNavigationWarning.value = false
  if (pendingConfigurationChange.value) {
    const target = pendingConfigurationChange.value
    pendingConfigurationChange.value = ''
    selectedConfiguration.value = target
    if (target === 'New Configuration') {
      resetToDefaults()
    } else {
      const cfg = await loadConfiguration(target)
      if (cfg) applyConfig(cfg)
    }
  }
}

function handleCancelNavigation() {
  showNavigationWarning.value = false
  pendingConfigurationChange.value = ''
}

function handleOpenSaveModal() {
  clearConfigurationSaveError()
  showSaveModal.value = true
  configurationName.value = ''
}

async function handleConfigurationChange(value: string) {
  if (hasUnsavedChanges.value && value !== selectedConfiguration.value) {
    pendingConfigurationChange.value = value
    showNavigationWarning.value = true
    return
  }
  selectedConfiguration.value = value
  if (value === 'New Configuration') {
    resetToDefaults()
    return
  }
  if (value && value !== '') {
    const cfg = await loadConfiguration(value)
    if (cfg) applyConfig(cfg)
  }
}

function requestDeleteConfiguration() {
  if (!selectedConfiguration.value || selectedConfiguration.value === 'New Configuration') return
  deleteError.value = ''
  showDeleteModal.value = true
}

async function confirmDeleteConfiguration() {
  const target = selectedConfiguration.value
  if (!target || target === 'New Configuration') {
    showDeleteModal.value = false
    return
  }
  deleting.value = true
  deleteError.value = ''
  const ok = await deleteConfiguration(target)
  deleting.value = false
  if (!ok) {
    deleteError.value = configurationSaveError.value || 'Failed to delete configuration.'
    return
  }
  showDeleteModal.value = false
  selectedConfiguration.value = ''
  hasUnsavedChanges.value = false
  resetToDefaults()
}

function cancelDeleteConfiguration() {
  showDeleteModal.value = false
  deleteError.value = ''
}

function handleBrightnessChange(value: number, phase: 'descent' | 'bottom' | 'ascent') {
  const currentBrightness = phase === 'descent' ? descentLightBrightness.value
    : phase === 'bottom' ? bottomLightBrightness.value
    : ascentLightBrightness.value
  if (value < 50 && currentBrightness >= 50) {
    pendingBrightness.value = { value, phase }
    showBrightnessWarning.value = true
  } else {
    if (phase === 'descent') descentLightBrightness.value = value
    else if (phase === 'bottom') bottomLightBrightness.value = value
    else ascentLightBrightness.value = value
    hasUnsavedChanges.value = true
  }
}

function confirmBrightnessChange() {
  if (pendingBrightness.value) {
    const { value, phase } = pendingBrightness.value
    if (phase === 'descent') descentLightBrightness.value = value
    else if (phase === 'bottom') bottomLightBrightness.value = value
    else ascentLightBrightness.value = value
    hasUnsavedChanges.value = true
  }
  showBrightnessWarning.value = false
  pendingBrightness.value = null
}

function cancelBrightnessChange() {
  showBrightnessWarning.value = false
  pendingBrightness.value = null
}

function handleDescentCameraToggle(checked: boolean) {
  descentCameraOn.value = checked
  descentLightOn.value = checked
  hasUnsavedChanges.value = true
}

function handleBottomCameraToggle(checked: boolean) {
  bottomCameraOn.value = checked
  bottomLightOn.value = checked
}

function handleAscentCameraToggle(checked: boolean) {
  ascentCameraOn.value = checked
  ascentLightOn.value = checked
}

async function handleOverwriteSave() {
  const payload = buildConfigPayload(selectedConfiguration.value)
  const saved = await saveConfiguration(payload)
  if (saved) {
    configurationName.value = ''
    showSaveModal.value = false
    hasUnsavedChanges.value = false
    if (pendingConfigurationChange.value) {
      selectedConfiguration.value = pendingConfigurationChange.value
      if (pendingConfigurationChange.value === 'New Configuration') resetToDefaults()
      pendingConfigurationChange.value = ''
      showNavigationWarning.value = false
    }
  }
}

function handleSaveAsNew() {
  const nextName = generateNextConfigName(selectedConfiguration.value)
  configurationName.value = nextName
}

function handleBeforeUnload(e: BeforeUnloadEvent) {
  if (hasUnsavedChanges.value) {
    e.preventDefault()
    e.returnValue = ''
  }
}

// ── Global camera settings + preset manager ─────────────────────────
//
// One global RadCam profile (image/video quality) applied via the
// br4kcam-manager proxy — not per dive phase. The Advanced panel exposes
// the full base/advanced setting surface for experimentation; presets can
// be saved, downloaded, imported, and marked "active" (the
// active preset is auto-applied at DORIS startup and at dive start).

const {
  settings: liveCameraSettings,
  loading: cameraLoading,
  applying: cameraApplying,
  error: cameraError,
  fetchSettings: fetchCameraSettings,
  applySettings,
  applyRecommended,
} = useCameraSettings()

const {
  presets,
  activePreset,
  error: presetError,
  fetchPresets,
  savePreset,
  deletePreset,
  applyPreset,
  downloadPreset,
  importPreset,
  fetchActivePreset,
  setActivePreset,
} = usePresets()

const cameraAdvancedOpen = ref(false)
const cameraStatus = ref('')
const newPresetName = ref('')
const importInput = ref<HTMLInputElement | null>(null)

// Editable copies of the live settings (composable refs are readonly).
const videoForm = ref<Record<string, number | undefined>>({})
const baseForm = ref<CameraBaseSettings>({})
const advancedForm = ref<CameraAdvancedSettings>({})

const CODEC_OPTIONS = [
  { value: 1, label: 'H.264' },
  { value: 5, label: 'H.265 (HEVC)' },
]

// Settings DORIS provides hardware defaults for on this vehicle — Day/Night &
// IR-Cut (H), Light/IR LED (I), Aperture/Iris (J) and Scene Mode (M). They are
// hidden from the editor only because there's no dedicated control for them
// yet; their values are still kept in the form data and stored JSON. The
// backend fills these defaults in only when a preset/apply omits them, so an
// advanced user can override any of them by editing a preset's JSON directly.
const DORIS_FIXED_CAMERA_KEYS = new Set<string>([
  // H. Day/Night & IR-Cut
  'color_black', 'infr_detect_mode', 'sens_day_to_night', 'sens_night_to_day',
  'infr_day_h', 'infr_day_m', 'infr_night_h', 'infr_night_m', 'ircut_level', 'ldr_level',
  // I. Light / IR LED Control
  'led_control_mode', 'lamp_type', 'led_control_avail', 'ir_level', 'led_level', 'led_control',
  // J. Aperture / Iris
  'auto_iris', 'irisLevel',
  // M. Scene Mode
  'scene_mode', 'sceneMode',
])

const resolutionOptions = computed(() => {
  const list = liveCameraSettings.value?.video.pixel_list ?? []
  return list.map(r => ({ value: `${r.width}x${r.height}`, label: `${r.width} × ${r.height}` }))
})

// Standard frame rates offered in the dropdown, capped to whatever the camera
// reports as max_framerate at the current resolution.  The camera's current
// value is always included so an out-of-list setting is never silently lost.
const FRAME_RATE_CHOICES = [5, 10, 15, 24, 25, 30, 50, 60]
const frameRateOptions = computed(() => {
  const max = liveCameraSettings.value?.video.max_framerate ?? 30
  const current = videoForm.value.frame_rate
  const values = new Set(FRAME_RATE_CHOICES.filter(v => v <= max))
  if (typeof current === 'number' && current > 0) values.add(current)
  return [...values].sort((a, b) => a - b).map(v => ({ value: v, label: `${v} fps` }))
})

// Bitrate presets in kbps (the camera's unit; 1 Mbps = 1024 kbps here, matching
// the recommended-bitrate sheet). Spans the sheet's Low/Medium/High tiers
// (4/8/16 Mbps @ 4K) up through the ~50 Mbps we actually run at 4K. The
// camera's current value is always kept so an out-of-list setting isn't lost.
const BITRATE_CHOICES_KBPS = [2048, 4096, 8192, 16384, 24576, 32768, 51200, 65536]
function bitrateLabel(kbps: number): string {
  const mbps = kbps / 1024
  return `${Number.isInteger(mbps) ? mbps : mbps.toFixed(1)} Mbps`
}
const bitrateOptions = computed(() => {
  const current = videoForm.value.bitrate
  const values = new Set(BITRATE_CHOICES_KBPS)
  if (typeof current === 'number' && current > 0) values.add(current)
  return [...values].sort((a, b) => a - b).map(v => ({ value: v, label: bitrateLabel(v) }))
})

const selectedResolution = computed({
  get: () => {
    const w = videoForm.value.pic_width
    const h = videoForm.value.pic_height
    return w && h ? `${w}x${h}` : ''
  },
  set: (val: string) => {
    const [w, h] = val.split('x').map(Number)
    videoForm.value.pic_width = w
    videoForm.value.pic_height = h
  },
})

// Reset/trigger keys aren't meaningful as editable fields.
const CAMERA_TRIGGER_KEYS = new Set<string>(['set_default', 'onceAWB'])

function isEditableCameraKey(k: string): boolean {
  return !DORIS_FIXED_CAMERA_KEYS.has(k) && !CAMERA_TRIGGER_KEYS.has(k)
}

type CameraGroup = 'base' | 'advanced' | 'video'
interface CameraFieldRow { key: string; group: CameraGroup; meta: ReturnType<typeof cameraFieldMeta> }

function cameraFormFor(group: CameraGroup): Record<string, number | undefined> {
  if (group === 'base') return baseForm.value
  if (group === 'advanced') return advancedForm.value
  return videoForm.value
}
function cameraFieldValue(f: CameraFieldRow): number | undefined {
  return cameraFormFor(f.group)[f.key]
}
function setCameraField(f: CameraFieldRow, raw: string) {
  const target = cameraFormFor(f.group)
  const n = raw === '' ? NaN : Number(raw)
  target[f.key] = Number.isNaN(n) ? undefined : n
}

// A few commonly-tuned image settings are promoted out of the Advanced editor
// and shown in the main quality panel: Auto White Balance, Exposure Strategy
// and Video Standard.  They're excluded from cameraSections below so they don't
// appear twice.
const PROMOTED_CAMERA_FIELDS: { key: string; group: CameraGroup }[] = [
  { key: 'auto_awb', group: 'base' },
  { key: 'AE_strategy_mode', group: 'base' },
  { key: 'power_freq', group: 'advanced' },
]
const PROMOTED_CAMERA_KEYS = new Set(
  PROMOTED_CAMERA_FIELDS.map(f => `${f.group}:${f.key}`),
)
const promotedCameraFields = computed<CameraFieldRow[]>(() =>
  PROMOTED_CAMERA_FIELDS
    .filter(f => f.key in cameraFormFor(f.group))
    .map(f => ({ key: f.key, group: f.group, meta: cameraFieldMeta(f.key, f.group) })),
)

// Group related controls into logical sections (all gain together, WB together,
// etc.) rather than a flat alphabetical list. Hidden/pinned/trigger keys are
// excluded; any editable key not covered by a section falls into "Other" so the
// camera's full surface stays reachable. DORIS-pinned keys are still kept in the
// form data (they round-trip into presets), just not shown here.
const cameraSections = computed<{ title: string; fields: CameraFieldRow[] }[]>(() => {
  const sections = CAMERA_SECTIONS.map(sec => ({
    title: sec.title,
    fields: sec.fields
      .filter(f =>
        isEditableCameraKey(f.key)
        && !PROMOTED_CAMERA_KEYS.has(`${f.group}:${f.key}`)
        && f.key in cameraFormFor(f.group))
      .map(f => ({ key: f.key, group: f.group, meta: cameraFieldMeta(f.key, f.group) })),
  })).filter(s => s.fields.length > 0)

  const others: CameraFieldRow[] = [
    ...Object.keys(baseForm.value)
      .filter(k => isEditableCameraKey(k) && !CAMERA_SECTION_KEYS.has(`base:${k}`))
      .map(k => ({ key: k, group: 'base' as CameraGroup, meta: cameraFieldMeta(k, 'base') })),
    ...Object.keys(advancedForm.value)
      .filter(k => isEditableCameraKey(k) && !CAMERA_SECTION_KEYS.has(`advanced:${k}`))
      .map(k => ({ key: k, group: 'advanced' as CameraGroup, meta: cameraFieldMeta(k, 'advanced') })),
  ]
  if (others.length) sections.push({ title: 'Other', fields: others })
  return sections
})

function syncCameraForms() {
  const s = liveCameraSettings.value
  if (!s) return
  videoForm.value = { ...s.video, pixel_list: undefined } as Record<string, number | undefined>
  // Drop read-only keys from the editable video form.
  delete videoForm.value.pixel_list
  delete videoForm.value.max_framerate
  baseForm.value = { ...s.base }
  advancedForm.value = { ...s.advanced }
  // Note: DORIS-pinned keys (H/I/J/M) are kept in the form data so they persist
  // in presets and the stored JSON; they're only hidden from the rendered
  // editor (see baseKeys/advancedKeys) and re-asserted server-side on apply.
}

function cleanNums(obj: Record<string, unknown>): Record<string, number> {
  const out: Record<string, number> = {}
  for (const [k, v] of Object.entries(obj)) {
    const n = Number(v)
    if (v !== undefined && v !== null && v !== '' && Number.isFinite(n)) out[k] = n
  }
  return out
}

function buildCameraBundle(): Partial<CameraSettingsBundle> {
  return {
    video: cleanNums(videoForm.value),
    base: cleanNums(baseForm.value),
    advanced: cleanNums(advancedForm.value),
  }
}

async function loadCameraSettings() {
  const s = await fetchCameraSettings()
  if (s) syncCameraForms()
}

async function applyGlobalCameraSettings() {
  cameraStatus.value = ''
  const fresh = await applySettings(buildCameraBundle())
  if (fresh) {
    syncCameraForms()
    cameraStatus.value = 'Camera settings applied.'
  }
}

async function applyRecommendedSettings() {
  cameraStatus.value = ''
  if (await applyRecommended()) {
    syncCameraForms()
    cameraStatus.value = 'Default settings applied.'
  }
}

async function saveCurrentAsPreset() {
  const name = newPresetName.value.trim()
  if (!name) return
  const saved = await savePreset({ name, ...buildCameraBundle() } as never)
  if (saved) {
    cameraStatus.value = `Preset "${name}" saved.`
    newPresetName.value = ''
  }
}

async function applyPresetByName(name: string) {
  cameraStatus.value = ''
  const fresh = await applyPreset(name)
  if (fresh) {
    await loadCameraSettings()
    cameraStatus.value = `Preset "${name}" applied.`
  }
}

async function deletePresetByName(name: string) {
  if (await deletePreset(name)) cameraStatus.value = `Preset "${name}" deleted.`
}

async function onActivePresetChange(name: string) {
  await setActivePreset(name || null)
}

function triggerPresetImport() {
  importInput.value?.click()
}

async function onPresetFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const saved = await importPreset(file)
  if (saved) cameraStatus.value = `Preset "${saved.name}" imported.`
  input.value = ''
}

onMounted(() => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  fetchConfigurations()
  void loadCameraSettings()
  void fetchPresets()
  void fetchActivePreset()
})

onUnmounted(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
})

const inputStyle = "background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
const phaseStyle = "background-color: rgba(14, 36, 70, 0.3); border: 1px solid rgba(65, 185, 195, 0.2)"
</script>

<template>
  <div class="max-w-6xl mx-auto px-4 py-6 md:py-8">
    <div class="backdrop-blur-sm rounded-xl p-6 border" style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)">
      <div class="mb-6">
        <h1 class="text-white text-2xl flex items-center gap-2">
          <Settings class="w-6 h-6" style="color: #96EEF2" />
          Deployment Configuration
        </h1>
      </div>

      <!-- Warnings -->
      <div v-if="warnings.length > 0" class="rounded-lg p-4 mb-6" style="background-color: rgba(255, 153, 55, 0.1); border: 1px solid rgba(255, 153, 55, 0.3)">
        <div class="flex items-start gap-3">
          <AlertTriangle class="w-5 h-5 flex-shrink-0 mt-0.5" style="color: #FF9937" />
          <div>
            <p class="mb-2" style="color: #FF9937">Configuration Warnings:</p>
            <ul class="text-sm space-y-1" style="color: #FCD869">
              <li v-for="(warning, index) in warnings" :key="index">• {{ warning }}</li>
            </ul>
          </div>
        </div>
      </div>

      <!-- Configuration Profile -->
      <div class="mb-6">
        <label class="block mb-2" style="color: #96EEF2">Load Configuration</label>
        <div class="flex items-stretch gap-2 mb-3">
          <select :value="selectedConfiguration" @change="handleConfigurationChange(($event.target as HTMLSelectElement).value)" class="flex-1 px-4 py-3 text-white rounded-lg focus:outline-none" :style="inputStyle">
            <option value="">-- Select Configuration --</option>
            <option value="New Configuration">New Configuration</option>
            <option v-for="(config, index) in savedConfigurations" :key="index" :value="config">{{ config }}</option>
          </select>
          <button
            v-if="selectedConfiguration && selectedConfiguration !== 'New Configuration'"
            type="button"
            @click="requestDeleteConfiguration"
            :title="`Delete configuration ${selectedConfiguration}`"
            aria-label="Delete configuration"
            class="px-4 rounded-lg text-white transition-all hover:opacity-90 flex items-center justify-center"
            style="background-color: rgba(221, 44, 29, 0.2); border: 1px solid rgba(221, 44, 29, 0.5)"
          >
            <Trash2 class="w-5 h-5" style="color: #FF6B5E" />
          </button>
        </div>
      </div>

      <!-- ==================== CAMERA SETTINGS (GLOBAL) ==================== -->
      <div class="mb-6 p-6 rounded-lg" :style="phaseStyle">
        <h2 class="text-white text-xl mb-2 flex items-center gap-2">
          <CameraIcon class="w-5 h-5" style="color: #96EEF2" />
          Camera Settings
        </h2>
        <p class="text-sm mb-4" style="color: rgba(150, 238, 242, 0.7)">
          One global image/video quality profile applied to the RadCam through the 4K Cam Manager. The active preset is re-applied automatically at startup and at the beginning of each dive.
        </p>

        <!-- Manager unreachable / loading states -->
        <div v-if="cameraLoading && !liveCameraSettings" class="text-sm" style="color: #96EEF2">
          Reading camera settings…
        </div>
        <div
          v-else-if="!liveCameraSettings"
          class="p-4 rounded-lg text-sm"
          style="background-color: rgba(221, 44, 29, 0.15); border: 1px solid rgba(221, 44, 29, 0.4); color: #FFB4AC"
        >
          <div class="flex items-start gap-2">
            <AlertTriangle class="w-5 h-5 flex-shrink-0" style="color: #FF6B5E" />
            <div>
              <p class="mb-1">Couldn't reach the 4K Cam Manager{{ cameraError ? ': ' + cameraError : '' }}.</p>
              <p class="mb-2" style="color: rgba(150, 238, 242, 0.7)">
                Make sure the br4kcam-manager extension is installed and set <code>DORIS_BR4KCAM_URL</code> to its reachable base URL. Presets can still be edited below.
              </p>
              <button @click="loadCameraSettings" class="px-3 py-1.5 rounded-lg text-white text-sm" style="background-color: rgba(65, 185, 195, 0.3); border: 1px solid rgba(65, 185, 195, 0.5)">Retry</button>
            </div>
          </div>
        </div>

        <!-- Quality controls -->
        <div v-if="liveCameraSettings" class="space-y-4">
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div v-if="resolutionOptions.length">
              <label class="block mb-2 text-sm" style="color: #96EEF2">Resolution</label>
              <select v-model="selectedResolution" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                <option v-for="opt in resolutionOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
              </select>
            </div>
            <div>
              <label class="block mb-2 text-sm" style="color: #96EEF2">Frame Rate</label>
              <select v-model.number="videoForm.frame_rate" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                <option v-for="opt in frameRateOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
              </select>
            </div>
            <div>
              <label class="block mb-2 text-sm" style="color: #96EEF2">Bitrate</label>
              <select v-model.number="videoForm.bitrate" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                <option v-for="opt in bitrateOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
              </select>
            </div>
            <div>
              <label class="block mb-2 text-sm" style="color: #96EEF2">Codec</label>
              <select v-model.number="videoForm.encode_type" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                <option v-for="opt in CODEC_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
              </select>
            </div>
            <div>
              <label class="block mb-2 text-sm" :title="'VBR lets the bitrate rise and fall with scene complexity; CBR holds it fixed for predictable file sizes.'" style="color: #96EEF2">Bitrate Mode</label>
              <select v-model.number="videoForm.rc_mode" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                <option :value="0">Variable (VBR)</option><option :value="1">Constant (CBR)</option>
              </select>
            </div>
            <div v-for="f in promotedCameraFields" :key="f.group + '-' + f.key">
              <label class="block mb-2 text-sm" :title="f.meta.help || f.key" style="color: #96EEF2">{{ f.meta.label }}</label>
              <select v-if="f.meta.kind === 'select'" :value="cameraFieldValue(f)" @change="setCameraField(f, ($event.target as HTMLSelectElement).value)" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                <option v-for="o in f.meta.options" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
              <input v-else type="number" :value="cameraFieldValue(f)" @input="setCameraField(f, ($event.target as HTMLInputElement).value)" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" />
            </div>
          </div>

          <div class="flex flex-wrap gap-3">
            <button @click="applyGlobalCameraSettings" :disabled="cameraApplying" class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 disabled:opacity-50" style="background: linear-gradient(135deg, #41B9C3 0%, #96EEF2 100%)">
              {{ cameraApplying ? 'Applying…' : 'Apply to Camera' }}
            </button>
            <button @click="loadCameraSettings" :disabled="cameraLoading || cameraApplying" class="px-4 py-2 text-white rounded-lg disabled:opacity-50" style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid rgba(65, 185, 195, 0.4)">
              {{ cameraLoading ? 'Reading…' : 'Query Current Settings' }}
            </button>
            <button @click="applyRecommendedSettings" :disabled="cameraApplying" class="px-4 py-2 text-white rounded-lg disabled:opacity-50" style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid rgba(65, 185, 195, 0.4)">
              Apply Default
            </button>
          </div>

          <!-- Advanced (experimental) -->
          <button @click="cameraAdvancedOpen = !cameraAdvancedOpen" class="flex items-center gap-2 px-4 py-2 mt-2 rounded-lg transition-all hover:opacity-80" style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid rgba(65, 185, 195, 0.4); color: #96EEF2">
            <ChevronUp v-if="cameraAdvancedOpen" class="w-5 h-5" />
            <ChevronDown v-else class="w-5 h-5" />
            <span class="font-medium">Advanced (experimental)</span>
          </button>
          <div v-if="cameraAdvancedOpen" class="p-4 rounded-lg space-y-4" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
            <p class="text-xs" style="color: rgba(150, 238, 242, 0.6)">
              The full image setting surface reported by the camera. Day/Night &amp; IR-Cut, LED, Iris and Scene Mode use DORIS hardware defaults and aren't shown here — they're still stored, and can be overridden by editing a preset's JSON directly.
            </p>
            <div v-for="section in cameraSections" :key="section.title">
              <h4 class="text-sm mb-2" style="color: #96EEF2">{{ section.title }}</h4>
              <div class="grid grid-cols-2 md:grid-cols-3 gap-4">
                <div v-for="f in section.fields" :key="f.group + '-' + f.key">
                  <label class="block mb-1 text-xs" :title="f.meta.help || f.key" style="color: rgba(150, 238, 242, 0.8)">{{ f.meta.label }}</label>
                  <select v-if="f.meta.kind === 'select'" :value="cameraFieldValue(f)" @change="setCameraField(f, ($event.target as HTMLSelectElement).value)" class="w-full px-3 py-1.5 text-white rounded-lg focus:outline-none text-sm" :style="inputStyle">
                    <option v-for="o in f.meta.options" :key="o.value" :value="o.value">{{ o.label }}</option>
                  </select>
                  <div v-else-if="f.meta.kind === 'slider'" class="flex items-center gap-2">
                    <input type="range" :min="f.meta.min" :max="f.meta.max" :step="f.meta.step || 1" :value="cameraFieldValue(f)" @input="setCameraField(f, ($event.target as HTMLInputElement).value)" class="flex-1" style="accent-color: #41B9C3" />
                    <input type="number" :min="f.meta.min" :max="f.meta.max" :value="cameraFieldValue(f)" @input="setCameraField(f, ($event.target as HTMLInputElement).value)" class="w-16 px-2 py-1 text-white rounded text-sm text-right" :style="inputStyle" />
                  </div>
                  <input v-else type="number" :value="cameraFieldValue(f)" @input="setCameraField(f, ($event.target as HTMLInputElement).value)" class="w-full px-3 py-1.5 text-white rounded-lg focus:outline-none text-sm" :style="inputStyle" />
                </div>
              </div>
            </div>
            <button @click="applyGlobalCameraSettings" :disabled="cameraApplying" class="px-4 py-2 text-white rounded-lg disabled:opacity-50" style="background: linear-gradient(135deg, #41B9C3 0%, #96EEF2 100%)">
              {{ cameraApplying ? 'Applying…' : 'Apply All Settings' }}
            </button>
          </div>
        </div>

        <!-- Preset manager -->
        <div class="mt-6 pt-4" style="border-top: 1px solid rgba(65, 185, 195, 0.2)">
          <h3 class="text-white flex items-center gap-2 mb-3" style="font-weight: 500">
            <Save class="w-4 h-4" style="color: #41B9C3" />
            Presets
          </h3>

          <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label class="block mb-2 text-sm" style="color: #96EEF2">Active preset (auto-applied at startup &amp; dive start)</label>
              <select
                :value="activePreset?.name ?? ''"
                @change="onActivePresetChange(($event.target as HTMLSelectElement).value)"
                class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                :style="inputStyle"
              >
                <option value="">None</option>
                <option v-for="p in presets" :key="p.name" :value="p.name">{{ p.name }}</option>
              </select>
            </div>
            <div>
              <label class="block mb-2 text-sm" style="color: #96EEF2">New preset name</label>
              <div class="flex gap-2">
                <input v-model="newPresetName" placeholder="e.g. Reef daylight" class="flex-1 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" />
              </div>
            </div>
          </div>

          <div class="flex flex-wrap gap-3 mb-4">
            <button @click="saveCurrentAsPreset" :disabled="!newPresetName.trim()" class="px-4 py-2 text-white rounded-lg disabled:opacity-50" style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid rgba(65, 185, 195, 0.4)" title="Save the fields shown above as a preset. Use “Query Current Settings” first to pull the camera's current values in.">
              Save Preset
            </button>
            <button @click="triggerPresetImport" class="px-4 py-2 text-white rounded-lg" style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid rgba(65, 185, 195, 0.4)">
              Import…
            </button>
            <input ref="importInput" type="file" accept="application/json,.json" class="hidden" @change="onPresetFileSelected" />
          </div>

          <div v-if="presets.length" class="space-y-2">
            <div
              v-for="p in presets"
              :key="p.name"
              class="flex items-center justify-between px-4 py-2 rounded-lg"
              style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)"
            >
              <span class="text-white text-sm">
                {{ p.name }}
                <span v-if="activePreset?.name === p.name" class="ml-2 text-xs px-2 py-0.5 rounded" style="background-color: rgba(65, 185, 195, 0.3); color: #96EEF2">active</span>
              </span>
              <div class="flex gap-2">
                <button @click="applyPresetByName(p.name)" :disabled="!liveCameraSettings" class="px-3 py-1 text-white rounded text-sm disabled:opacity-50" style="background-color: rgba(65, 185, 195, 0.25)">Apply</button>
                <button @click="downloadPreset(p.name)" class="px-3 py-1 text-white rounded text-sm" style="background-color: rgba(65, 185, 195, 0.15)">Download</button>
                <button @click="deletePresetByName(p.name)" class="px-3 py-1 rounded text-sm" style="background-color: rgba(221, 44, 29, 0.2); color: #FF6B5E">Delete</button>
              </div>
            </div>
          </div>
          <p v-else class="text-sm" style="color: rgba(150, 238, 242, 0.6)">No presets saved yet.</p>
        </div>

        <p v-if="cameraStatus" class="mt-3 text-sm" style="color: #96EEF2">{{ cameraStatus }}</p>
        <p v-if="presetError" class="mt-1 text-sm" style="color: #FF6B5E">{{ presetError }}</p>
      </div>

      <!-- ==================== DESCENT SECTION ==================== -->
      <div class="mb-6 p-6 rounded-lg" :style="phaseStyle">
        <h2 class="text-white text-xl mb-2 flex items-center gap-2">
          <ArrowDown class="w-5 h-5" style="color: #96EEF2" />
          Descent
        </h2>
        <p class="text-sm mb-6" style="color: rgba(150, 238, 242, 0.7)">
          Settings for camera, lighting, and data gathering during descent. Descent phase begins when DORIS detects it has been placed in the water. These settings will update to On Bottom programming when the seafloor is reached.
        </p>

        <!-- Descent Camera -->
        <div class="mb-6">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-white flex items-center gap-2" style="font-weight: 500">
              <CameraIcon class="w-4 h-4" style="color: #41B9C3" />
              Camera
            </h3>
            <label class="relative inline-flex items-center cursor-pointer">
              <input type="checkbox" :checked="descentCameraOn" @change="handleDescentCameraToggle(($event.target as HTMLInputElement).checked)" class="sr-only peer" />
              <div class="w-11 h-6 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all" :style="{ backgroundColor: descentCameraOn ? '#41B9C3' : 'rgba(65, 185, 195, 0.3)' }"></div>
              <span class="ml-3 text-sm" style="color: #96EEF2">{{ descentCameraOn ? 'On' : 'Off' }}</span>
            </label>
          </div>

          <div v-if="descentCameraOn" class="space-y-4 pl-6">
            <div>
              <label class="block mb-2 text-sm" style="color: #96EEF2">Image Capture Type</label>
              <div class="flex flex-col sm:flex-row gap-3 sm:gap-4">
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="radio" value="continuous-video" v-model="descentCameraType" @change="hasUnsavedChanges = true" class="w-4 h-4" />
                  <span style="color: #96EEF2">Continuous Video</span>
                </label>
                <label class="flex items-center gap-2 cursor-not-allowed opacity-40">
                  <input type="radio" value="video-interval" v-model="descentCameraType" disabled class="w-4 h-4" />
                  <span style="color: #96EEF2">Interval Video</span>
                </label>
                <label class="flex items-center gap-2 cursor-not-allowed opacity-40">
                  <input type="radio" value="timelapse" v-model="descentCameraType" disabled class="w-4 h-4" />
                  <span style="color: #96EEF2">Timelapse Images</span>
                </label>
              </div>
              <p class="mt-2 text-xs italic" style="color: rgba(150, 238, 242, 0.5)">
                Interval Video and Timelapse Images are only supported for the Bottom phase.
              </p>
            </div>

            <!-- Timelapse: Capture Frequency -->
            <div v-if="descentCameraType === 'timelapse'">
              <label class="block mb-2 text-sm" style="color: #96EEF2">Capture Frequency</label>
              <div class="flex gap-2">
                <input type="number" min="1" v-model.number="descentCaptureFrequency" @input="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" />
                <select v-model="descentCaptureFrequencyUnit" @change="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                  <option value="seconds">Seconds</option>
                  <option value="minutes">Minutes</option>
                  <option value="hours">Hours</option>
                </select>
              </div>
              <div v-if="descentCaptureFrequencyTooLow" class="mt-3 rounded-lg p-4" style="background-color: #0E2446; border: 2px solid #DD2C1D">
                <div class="flex items-start gap-3">
                  <AlertTriangle class="w-5 h-5 flex-shrink-0 mt-0.5" style="color: #DD2C1D" />
                  <div class="flex-1">
                    <h3 class="text-white font-semibold mb-1">Low Frequency Warning</h3>
                    <p class="text-white text-sm opacity-90">This frequency setting may not capture any images given descent time.</p>
                  </div>
                </div>
              </div>
            </div>

            <!-- Interval Video Settings -->
            <div v-else-if="descentCameraType === 'video-interval'" class="p-4 rounded-lg space-y-4" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
              <h4 class="text-sm" style="color: #96EEF2">Interval Settings</h4>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Record for</label>
                <div class="flex gap-2">
                  <input type="number" v-model="descentVideoRecordNumber" @input="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" min="1" />
                  <select v-model="descentVideoRecordUnit" @change="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                    <option value="seconds">seconds</option>
                    <option value="minutes">minutes</option>
                    <option value="hours">hours</option>
                  </select>
                </div>
                <div v-if="isRecordTooLong(descentVideoRecordNumber, descentVideoRecordUnit)" class="mt-3 rounded-lg p-4" style="background-color: #0E2446; border: 2px solid #DD2C1D">
                  <div class="flex items-start gap-3">
                    <AlertTriangle class="w-5 h-5 flex-shrink-0 mt-0.5" style="color: #DD2C1D" />
                    <div class="flex-1">
                      <h3 class="text-white font-semibold mb-1">Low Frequency Warning</h3>
                      <p class="text-white text-sm opacity-90">This record duration may not capture sufficient video given descent time.</p>
                    </div>
                  </div>
                </div>
              </div>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Pause for</label>
                <div class="flex gap-2">
                  <input type="number" v-model="descentVideoPauseNumber" @input="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" min="1" />
                  <select v-model="descentVideoPauseUnit" @change="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                    <option value="seconds">seconds</option>
                    <option value="minutes">minutes</option>
                    <option value="hours">hours</option>
                  </select>
                </div>
                <div v-if="isRecordTooLong(descentVideoPauseNumber, descentVideoPauseUnit)" class="mt-3 rounded-lg p-4" style="background-color: #0E2446; border: 2px solid #DD2C1D">
                  <div class="flex items-start gap-3">
                    <AlertTriangle class="w-5 h-5 flex-shrink-0 mt-0.5" style="color: #DD2C1D" />
                    <div class="flex-1">
                      <h3 class="text-white font-semibold mb-1">Low Frequency Warning</h3>
                      <p class="text-white text-sm opacity-90">This pause duration may not capture sufficient video given descent time.</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- One-shot white balance -->
            <div class="pt-2" style="border-top: 1px solid rgba(65, 185, 195, 0.2)">
              <div class="flex items-center justify-between">
                <div class="pr-4">
                  <label class="block text-sm" style="color: #96EEF2">Auto White Balance on Lights</label>
                  <p class="text-xs mt-1 opacity-70" style="color: #96EEF2">
                    Fires a one-time white balance a couple seconds after the descent lights turn on, so colors are calibrated for the lit scene. If the descent light is off, it runs immediately when descent begins.
                  </p>
                </div>
                <label class="relative inline-flex items-center cursor-pointer flex-shrink-0">
                  <input type="checkbox" v-model="descentAutoWhiteBalance" class="sr-only peer" />
                  <div class="w-11 h-6 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all" :style="{ backgroundColor: descentAutoWhiteBalance ? '#41B9C3' : 'rgba(65, 185, 195, 0.3)' }"></div>
                  <span class="ml-3 text-sm" style="color: #96EEF2">{{ descentAutoWhiteBalance ? 'On' : 'Off' }}</span>
                </label>
              </div>
            </div>

            <!-- Sleep Timer (disabled) -->
            <div class="mt-4 opacity-40">
              <label class="flex items-center gap-2 mb-2 text-sm cursor-not-allowed" style="color: #96EEF2">
                <input type="checkbox" disabled class="w-4 h-4 cursor-not-allowed" style="accent-color: #41B9C3" />
                Optional: Stop recording and go to sleep after elapsed time of:
              </label>
            </div>

          </div>
        </div>

        <!-- Descent Light -->
        <div class="mb-6">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-white flex items-center gap-2" style="font-weight: 500">
              <Lightbulb class="w-4 h-4" style="color: #41B9C3" />
              Light
            </h3>
            <label class="relative inline-flex items-center cursor-pointer">
              <input type="checkbox" v-model="descentLightOn" class="sr-only peer" />
              <div class="w-11 h-6 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all" :style="{ backgroundColor: descentLightOn ? '#41B9C3' : 'rgba(65, 185, 195, 0.3)' }"></div>
              <span class="ml-3 text-sm" style="color: #96EEF2">{{ descentLightOn ? 'On' : 'Off' }}</span>
            </label>
          </div>

          <div v-if="descentLightOn" class="pl-6">
            <div v-if="descentCameraOn && (descentCameraType === 'timelapse' || descentCameraType === 'video-interval')" class="mb-4 p-4 rounded-lg" style="background-color: rgba(65, 185, 195, 0.1); border: 1px solid rgba(65, 185, 195, 0.3)">
              <p class="text-sm" style="color: #96EEF2">
                You have {{ descentCameraType === 'timelapse' ? 'Timelapse Images' : 'Interval Video' }} selected. Light will automatically {{ descentCameraType === 'timelapse' ? 'strobe to match camera frequency' : 'turn on to match camera frequency' }}.
              </p>
            </div>
            <div v-else class="mb-4">
              <label class="block mb-2 text-sm" style="color: #96EEF2">Light Mode</label>
              <div class="flex gap-4">
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="radio" value="continuous" v-model="descentLightMode" class="w-4 h-4" />
                  <span style="color: #96EEF2">Continuous Light</span>
                </label>
                <label class="flex items-center gap-2 cursor-not-allowed opacity-40">
                  <input type="radio" value="interval" v-model="descentLightMode" disabled class="w-4 h-4" />
                  <span style="color: #96EEF2">Interval Light</span>
                </label>
              </div>
            </div>

            <div v-if="descentLightMode === 'interval' && !(descentCameraOn && (descentCameraType === 'timelapse' || descentCameraType === 'video-interval'))" class="mb-4 p-4 rounded-lg space-y-4" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
              <h4 class="text-sm" style="color: #96EEF2">Interval Settings</h4>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Light On for</label>
                <div class="flex gap-2">
                  <input type="number" v-model="descentLightOnNumber" @input="hasUnsavedChanges = true" :disabled="descentMatchCameraInterval" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle + '; opacity: ' + (descentMatchCameraInterval ? '0.5' : '1')" min="1" />
                  <select v-model="descentLightOnUnit" @change="hasUnsavedChanges = true" :disabled="descentMatchCameraInterval" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle + '; opacity: ' + (descentMatchCameraInterval ? '0.5' : '1')">
                    <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                  </select>
                </div>
              </div>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Light Off for</label>
                <div class="flex gap-2">
                  <input type="number" v-model="descentLightOffNumber" @input="hasUnsavedChanges = true" :disabled="descentMatchCameraInterval" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle + '; opacity: ' + (descentMatchCameraInterval ? '0.5' : '1')" min="1" />
                  <select v-model="descentLightOffUnit" @change="hasUnsavedChanges = true" :disabled="descentMatchCameraInterval" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle + '; opacity: ' + (descentMatchCameraInterval ? '0.5' : '1')">
                    <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                  </select>
                </div>
              </div>
            </div>

            <label class="block mb-2 text-sm" style="color: #96EEF2">Light Brightness</label>
            <input type="range" min="0" max="100" :value="descentLightBrightness" @input="handleBrightnessChange(Number(($event.target as HTMLInputElement).value), 'descent')" class="w-full" />
            <div class="flex justify-between text-sm mt-1" style="color: #96EEF2">
              <span>0%</span><span>{{ descentLightBrightness }}%</span><span>100%</span>
            </div>
          </div>
        </div>

        <!-- Descent Data -->
        <div class="mb-6">
          <div class="mb-4">
            <h3 class="text-white flex items-center gap-2" style="font-weight: 500">
              <DatabaseIcon class="w-4 h-4" style="color: #41B9C3" />
              Data
            </h3>
          </div>
          <div class="pl-6">
            <p class="text-sm" style="color: rgba(150, 238, 242, 0.7)">Data collection is always on at the default sampling rate for each sensor. For sensor calibration, go to the Sensors page.</p>
          </div>
        </div>
      </div>

      <!-- ==================== ON BOTTOM SECTION ==================== -->
      <div class="mb-6 p-6 rounded-lg" :style="phaseStyle">
        <h2 class="text-white text-xl mb-2 flex items-center gap-2">
          <Anchor class="w-5 h-5" style="color: #96EEF2" />
          On Bottom
        </h2>
        <p class="text-sm mb-6" style="color: rgba(150, 238, 242, 0.7)">
          Settings for camera, lighting, and data gathering during bottom time. Bottom time is determined when the depth value is stable for 1 minute. These settings will automatically update when the weight release is triggered and the Ascent Phase begins.
        </p>

        <!-- Bottom Camera -->
        <div class="mb-6">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-white flex items-center gap-2" style="font-weight: 500">
              <CameraIcon class="w-4 h-4" style="color: #41B9C3" />
              Camera
            </h3>
            <label class="relative inline-flex items-center cursor-pointer">
              <input type="checkbox" :checked="bottomCameraOn" @change="handleBottomCameraToggle(($event.target as HTMLInputElement).checked)" class="sr-only peer" />
              <div class="w-11 h-6 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all" :style="{ backgroundColor: bottomCameraOn ? '#41B9C3' : 'rgba(65, 185, 195, 0.3)' }"></div>
              <span class="ml-3 text-sm" style="color: #96EEF2">{{ bottomCameraOn ? 'On' : 'Off' }}</span>
            </label>
          </div>

          <div v-if="bottomCameraOn" class="space-y-4 pl-6">
            <div>
              <label class="block mb-2 text-sm" style="color: #96EEF2">Delay Camera Start</label>
              <div class="flex gap-2">
                <input type="number" v-model="bottomCameraDelayNumber" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" min="0" />
                <select v-model="bottomCameraDelayUnit" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                  <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                </select>
              </div>
              <div v-if="isDelayTooLong(bottomCameraDelayNumber, bottomCameraDelayUnit)" class="mt-3 rounded-lg p-4" style="background-color: #0E2446; border: 2px solid #DD2C1D">
                <div class="flex items-start gap-3">
                  <AlertTriangle class="w-5 h-5 flex-shrink-0 mt-0.5" style="color: #DD2C1D" />
                  <div class="flex-1">
                    <h3 class="text-white font-semibold mb-1">Low Frequency Warning</h3>
                    <p class="text-white text-sm opacity-90">This delay duration may result in insufficient data capture during bottom time.</p>
                  </div>
                </div>
              </div>
            </div>

            <div>
              <label class="block mb-2 text-sm" style="color: #96EEF2">Image Capture Type</label>
              <div class="flex flex-col sm:flex-row gap-3 sm:gap-4">
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="radio" value="continuous-video" v-model="bottomCameraType" @change="hasUnsavedChanges = true" class="w-4 h-4" />
                  <span style="color: #96EEF2">Continuous Video</span>
                </label>
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="radio" value="video-interval" v-model="bottomCameraType" @change="hasUnsavedChanges = true" class="w-4 h-4" />
                  <span style="color: #96EEF2">Interval Video</span>
                </label>
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="radio" value="timelapse" v-model="bottomCameraType" @change="hasUnsavedChanges = true" class="w-4 h-4" />
                  <span style="color: #96EEF2">Timelapse Images</span>
                </label>
              </div>
            </div>

            <div v-if="bottomCameraType === 'timelapse'" class="space-y-4">
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Capture Frequency</label>
                <div class="flex gap-2">
                  <input type="number" min="1" v-model.number="bottomCaptureFrequency" @input="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" />
                  <select v-model="bottomCaptureFrequencyUnit" @change="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                    <option value="seconds">Seconds</option><option value="minutes">Minutes</option><option value="hours">Hours</option>
                  </select>
                </div>
              </div>
              <div class="p-4 rounded-lg space-y-3" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
                <h4 class="text-sm" style="color: #96EEF2">Light Strobe Around Each Snap</h4>
                <p class="text-xs" style="color: rgba(150, 238, 242, 0.7)">
                  Light turns on this many seconds before each snapshot, holds while the picture is taken,
                  then stays on for this many seconds after, before going dark again until the next snap.
                  Effective minimum capture frequency is {{ bottomTimelapseMinSeconds }} s.
                </p>
                <div class="flex gap-2 items-end">
                  <div class="flex-1">
                    <label class="block mb-1 text-xs" style="color: #96EEF2">Light on before snap (s)</label>
                    <input type="number" min="0" step="1" v-model="bottomTimelapseLightPreNumber" @input="hasUnsavedChanges = true" class="w-full px-3 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" />
                  </div>
                  <div class="flex-1">
                    <label class="block mb-1 text-xs" style="color: #96EEF2">Light on after snap (s)</label>
                    <input type="number" min="0" step="1" v-model="bottomTimelapseLightPostNumber" @input="hasUnsavedChanges = true" class="w-full px-3 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" />
                  </div>
                </div>
                <p v-if="bottomTimelapseTooFast" class="text-xs" style="color: #FF8888">
                  Capture Frequency ({{ bottomCaptureFrequencySeconds }} s) is shorter than the strobe window
                  ({{ bottomTimelapseMinSeconds }} s); the light will stay on continuously between snaps.
                </p>
              </div>
            </div>

            <div v-else-if="bottomCameraType === 'video-interval'" class="p-4 rounded-lg space-y-4" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
              <h4 class="text-sm" style="color: #96EEF2">Interval Settings</h4>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Record for</label>
                <div class="flex gap-2">
                  <input type="number" v-model="bottomVideoRecordNumber" @input="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" min="1" />
                  <select v-model="bottomVideoRecordUnit" @change="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                    <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                  </select>
                </div>
              </div>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Pause for</label>
                <div class="flex gap-2">
                  <input type="number" v-model="bottomVideoPauseNumber" @input="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" min="1" />
                  <select v-model="bottomVideoPauseUnit" @change="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                    <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                  </select>
                </div>
              </div>
            </div>

            <!-- One-shot white balance -->
            <div class="pt-2" style="border-top: 1px solid rgba(65, 185, 195, 0.2)">
              <div class="flex items-center justify-between">
                <div class="pr-4">
                  <label class="block text-sm" style="color: #96EEF2">Auto White Balance on Lights</label>
                  <p class="text-xs mt-1 opacity-70" style="color: #96EEF2">
                    Fires a one-time white balance a couple seconds after the bottom lights turn on, so colors are calibrated for the lit scene. If the bottom light is off, it runs immediately when the vehicle reaches the bottom.
                  </p>
                </div>
                <label class="relative inline-flex items-center cursor-pointer flex-shrink-0">
                  <input type="checkbox" v-model="bottomAutoWhiteBalance" class="sr-only peer" />
                  <div class="w-11 h-6 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all" :style="{ backgroundColor: bottomAutoWhiteBalance ? '#41B9C3' : 'rgba(65, 185, 195, 0.3)' }"></div>
                  <span class="ml-3 text-sm" style="color: #96EEF2">{{ bottomAutoWhiteBalance ? 'On' : 'Off' }}</span>
                </label>
              </div>
            </div>

            <!-- Sleep Timer (disabled) -->
            <div class="mt-4 opacity-40">
              <label class="flex items-center gap-2 mb-2 text-sm cursor-not-allowed" style="color: #96EEF2">
                <input type="checkbox" disabled class="w-4 h-4 cursor-not-allowed" style="accent-color: #41B9C3" />
                Optional: Stop recording and go to sleep after elapsed time of:
              </label>
            </div>

          </div>
        </div>

        <!-- Bottom Light -->
        <div class="mb-6">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-white flex items-center gap-2" style="font-weight: 500">
              <Lightbulb class="w-4 h-4" style="color: #41B9C3" />
              Light
            </h3>
            <label class="relative inline-flex items-center cursor-pointer">
              <input type="checkbox" v-model="bottomLightOn" class="sr-only peer" />
              <div class="w-11 h-6 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all" :style="{ backgroundColor: bottomLightOn ? '#41B9C3' : 'rgba(65, 185, 195, 0.3)' }"></div>
              <span class="ml-3 text-sm" style="color: #96EEF2">{{ bottomLightOn ? 'On' : 'Off' }}</span>
            </label>
          </div>

          <div v-if="bottomLightOn" class="space-y-4 pl-6">
            <div v-if="bottomCameraType !== 'timelapse'">
              <label class="block mb-2 text-sm" style="color: #96EEF2">Delay Light Start</label>
              <div class="flex gap-2">
                <input type="number" v-model="bottomLightDelayNumber" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" min="0" />
                <select v-model="bottomLightDelayUnit" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                  <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                </select>
              </div>
              <div v-if="isDelayTooLong(bottomLightDelayNumber, bottomLightDelayUnit)" class="mt-3 rounded-lg p-4" style="background-color: #0E2446; border: 2px solid #DD2C1D">
                <div class="flex items-start gap-3">
                  <AlertTriangle class="w-5 h-5 flex-shrink-0 mt-0.5" style="color: #DD2C1D" />
                  <div class="flex-1">
                    <h3 class="text-white font-semibold mb-1">Low Frequency Warning</h3>
                    <p class="text-white text-sm opacity-90">This delay duration may result in insufficient lighting during bottom time.</p>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="bottomCameraOn && (bottomCameraType === 'timelapse' || bottomCameraType === 'video-interval')" class="mb-4 p-4 rounded-lg" style="background-color: rgba(65, 185, 195, 0.1); border: 1px solid rgba(65, 185, 195, 0.3)">
              <p class="text-sm" style="color: #96EEF2">
                You have {{ bottomCameraType === 'timelapse' ? 'Timelapse Images' : 'Interval Video' }} selected. Light will automatically {{ bottomCameraType === 'timelapse' ? 'strobe to match camera frequency' : 'turn on to match camera frequency' }}.
              </p>
            </div>
            <div v-else>
              <label class="block mb-2 text-sm" style="color: #96EEF2">Light Mode</label>
              <div class="flex gap-4">
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="radio" value="continuous" v-model="bottomLightMode" class="w-4 h-4" />
                  <span style="color: #96EEF2">Continuous Light</span>
                </label>
                <label class="flex items-center gap-2 cursor-not-allowed opacity-40">
                  <input type="radio" value="interval" v-model="bottomLightMode" disabled class="w-4 h-4" />
                  <span style="color: #96EEF2">Interval Light</span>
                </label>
              </div>
            </div>

            <div v-if="bottomLightMode === 'interval' && !(bottomCameraOn && (bottomCameraType === 'timelapse' || bottomCameraType === 'video-interval'))" class="p-4 rounded-lg space-y-4" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
              <h4 class="text-sm" style="color: #96EEF2">Interval Settings</h4>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Light On for</label>
                <div class="flex gap-2">
                  <input type="number" v-model="bottomLightOnNumber" :disabled="bottomMatchCameraInterval" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle + '; opacity: ' + (bottomMatchCameraInterval ? '0.5' : '1')" min="1" />
                  <select v-model="bottomLightOnUnit" :disabled="bottomMatchCameraInterval" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle + '; opacity: ' + (bottomMatchCameraInterval ? '0.5' : '1')">
                    <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                  </select>
                </div>
              </div>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Light Off for</label>
                <div class="flex gap-2">
                  <input type="number" v-model="bottomLightOffNumber" :disabled="bottomMatchCameraInterval" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle + '; opacity: ' + (bottomMatchCameraInterval ? '0.5' : '1')" min="1" />
                  <select v-model="bottomLightOffUnit" :disabled="bottomMatchCameraInterval" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle + '; opacity: ' + (bottomMatchCameraInterval ? '0.5' : '1')">
                    <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                  </select>
                </div>
              </div>
            </div>

            <div>
              <label class="block mb-2 text-sm" style="color: #96EEF2">Light Brightness</label>
              <input type="range" min="0" max="100" :value="bottomLightBrightness" @input="handleBrightnessChange(Number(($event.target as HTMLInputElement).value), 'bottom')" class="w-full" />
              <div class="flex justify-between text-sm mt-1" style="color: #96EEF2">
                <span>0%</span><span>{{ bottomLightBrightness }}%</span><span>100%</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Bottom Data -->
        <div class="mb-6">
          <div class="mb-4">
            <h3 class="text-white flex items-center gap-2" style="font-weight: 500">
              <DatabaseIcon class="w-4 h-4" style="color: #41B9C3" />
              Data
            </h3>
          </div>
          <div class="pl-6">
            <p class="text-sm" style="color: rgba(150, 238, 242, 0.7)">Data collection is always on at the default sampling rate for each sensor. For sensor calibration, go to the Sensors page.</p>
          </div>
        </div>
      </div>

      <!-- ==================== ASCENT SECTION ==================== -->
      <div class="mb-6 p-6 rounded-lg" :style="phaseStyle">
        <h2 class="text-white text-xl mb-2 flex items-center gap-2">
          <ArrowUp class="w-5 h-5" style="color: #96EEF2" />
          Ascent
        </h2>
        <p class="text-sm mb-6" style="color: rgba(150, 238, 242, 0.7)">
          Settings for weight release, and camera, lighting, and data gathering during ascent. Ascent begins when the release begins to burn. Burn can take 20-30 minutes before DORIS leaves the seafloor.
        </p>

        <!-- Release Weight -->
        <div class="mb-6">
          <h3 class="text-white flex items-center gap-2 mb-4">
            <ArrowUp class="w-4 h-4" style="color: #41B9C3" />
            Release Weight
          </h3>
          <div class="space-y-4 pl-6">
            <div class="space-y-3">
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="radio" value="elapsed" :checked="releaseWeightBy === 'elapsed'" @change="emit('update:releaseWeightBy', 'elapsed')" class="w-4 h-4" />
                <span style="color: #96EEF2">By Elapsed Time on Bottom</span>
              </label>
              <div v-if="releaseWeightBy === 'elapsed'" class="pl-6 space-y-3">
                <div class="flex gap-2">
                  <input type="number" v-model="releaseWeightElapsedNumber" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" min="0" />
                  <select v-model="releaseWeightElapsedUnit" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                    <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                  </select>
                </div>
                <p class="text-xs italic" style="color: rgba(150, 238, 242, 0.5)">
                  Time spent on the bottom before the weight is released. Counted from when DORIS reaches the seafloor, not from launch.
                </p>
                <div v-if="releaseWeightWarning.show" class="mt-3 rounded-lg p-4" :style="releaseWeightWarning.severity === 'warning' ? 'background-color: rgba(255, 184, 0, 0.1); border: 1px solid rgba(255, 184, 0, 0.5)' : 'background-color: #0E2446; border: 2px solid #DD2C1D'">
                  <div class="flex items-start gap-3">
                    <AlertTriangle class="w-5 h-5 flex-shrink-0 mt-0.5" :style="releaseWeightWarning.severity === 'warning' ? 'color: #FFB800' : 'color: #DD2C1D'" />
                    <div class="flex-1">
                      <h3 class="text-white font-semibold mb-1">{{ releaseWeightWarning.title }}</h3>
                      <p class="text-white text-sm opacity-90">{{ releaseWeightWarning.message }}</p>
                    </div>
                  </div>
                </div>
              </div>
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="radio" value="datetime" :checked="releaseWeightBy === 'datetime'" @change="emit('update:releaseWeightBy', 'datetime')" class="w-4 h-4" />
                <span style="color: #96EEF2">By Date/Time</span>
              </label>
              <div v-if="releaseWeightBy === 'datetime'" class="pl-6 space-y-3">
                <div class="rounded-lg p-4" style="background-color: rgba(65, 185, 195, 0.15); border: 1px solid #41B9C3">
                  <div class="flex items-start gap-3">
                    <AlertTriangle class="w-5 h-5 flex-shrink-0 mt-0.5" style="color: #41B9C3" />
                    <div class="flex-1">
                      <p class="text-white text-sm">Program your date and time variables when you load the Configuration on the Dashboard.</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <template v-if="!ascentSameAsDescent">
          <!-- Ascent Camera -->
          <div class="mb-6">
            <div class="flex items-center justify-between mb-4">
              <h3 class="text-white flex items-center gap-2" style="font-weight: 500">
                <CameraIcon class="w-4 h-4" style="color: #41B9C3" />
                Camera
              </h3>
              <label class="relative inline-flex items-center cursor-pointer">
                <input type="checkbox" :checked="ascentCameraOn" @change="handleAscentCameraToggle(($event.target as HTMLInputElement).checked)" class="sr-only peer" />
                <div class="w-11 h-6 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all" :style="{ backgroundColor: ascentCameraOn ? '#41B9C3' : 'rgba(65, 185, 195, 0.3)' }"></div>
                <span class="ml-3 text-sm" style="color: #96EEF2">{{ ascentCameraOn ? 'On' : 'Off' }}</span>
              </label>
            </div>

            <div v-if="ascentCameraOn" class="space-y-4 pl-6">
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Image Capture Type</label>
                <div class="flex flex-col sm:flex-row gap-3 sm:gap-4">
                  <label class="flex items-center gap-2 cursor-pointer">
                    <input type="radio" value="continuous-video" v-model="ascentCameraType" @change="hasUnsavedChanges = true" class="w-4 h-4" />
                    <span style="color: #96EEF2">Continuous Video</span>
                  </label>
                  <label class="flex items-center gap-2 cursor-not-allowed opacity-40">
                    <input type="radio" value="video-interval" v-model="ascentCameraType" disabled class="w-4 h-4" />
                    <span style="color: #96EEF2">Interval Video</span>
                  </label>
                  <label class="flex items-center gap-2 cursor-not-allowed opacity-40">
                    <input type="radio" value="timelapse" v-model="ascentCameraType" disabled class="w-4 h-4" />
                    <span style="color: #96EEF2">Timelapse Images</span>
                  </label>
                </div>
                <p class="mt-2 text-xs italic" style="color: rgba(150, 238, 242, 0.5)">
                  Interval Video and Timelapse Images are only supported for the Bottom phase.
                </p>
              </div>

              <div v-if="ascentCameraType === 'timelapse'">
                <label class="block mb-2 text-sm" style="color: #96EEF2">Capture Frequency</label>
                <div class="flex gap-2">
                  <input type="number" min="1" v-model.number="ascentCaptureFrequency" @input="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" />
                  <select v-model="ascentCaptureFrequencyUnit" @change="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                    <option value="seconds">Seconds</option><option value="minutes">Minutes</option><option value="hours">Hours</option>
                  </select>
                </div>
              </div>

              <div v-else-if="ascentCameraType === 'video-interval'" class="p-4 rounded-lg space-y-4" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
                <h4 class="text-sm" style="color: #96EEF2">Interval Settings</h4>
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">Record for</label>
                  <div class="flex gap-2">
                    <input type="number" v-model="ascentVideoRecordNumber" @input="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" min="1" />
                    <select v-model="ascentVideoRecordUnit" @change="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                      <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                    </select>
                  </div>
                </div>
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">Pause for</label>
                  <div class="flex gap-2">
                    <input type="number" v-model="ascentVideoPauseNumber" @input="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" min="1" />
                    <select v-model="ascentVideoPauseUnit" @change="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                      <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                    </select>
                  </div>
                </div>
              </div>

              <!-- One-shot white balance -->
              <div class="pt-2" style="border-top: 1px solid rgba(65, 185, 195, 0.2)">
                <div class="flex items-center justify-between">
                  <div class="pr-4">
                    <label class="block text-sm" style="color: #96EEF2">Auto White Balance on Lights</label>
                    <p class="text-xs mt-1 opacity-70" style="color: #96EEF2">
                      Fires a one-time white balance a couple seconds after the ascent lights turn on, so colors are calibrated for the lit scene. If the ascent light is off, it runs immediately when ascent begins.
                    </p>
                  </div>
                  <label class="relative inline-flex items-center cursor-pointer flex-shrink-0">
                    <input type="checkbox" v-model="ascentAutoWhiteBalance" class="sr-only peer" />
                    <div class="w-11 h-6 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all" :style="{ backgroundColor: ascentAutoWhiteBalance ? '#41B9C3' : 'rgba(65, 185, 195, 0.3)' }"></div>
                    <span class="ml-3 text-sm" style="color: #96EEF2">{{ ascentAutoWhiteBalance ? 'On' : 'Off' }}</span>
                  </label>
                </div>
              </div>

              <!-- Sleep Timer (disabled) -->
              <div class="mt-4 opacity-40">
                <label class="flex items-center gap-2 mb-2 text-sm cursor-not-allowed" style="color: #96EEF2">
                  <input type="checkbox" disabled class="w-4 h-4 cursor-not-allowed" style="accent-color: #41B9C3" />
                  Optional: Stop recording and go to sleep after elapsed time of:
                </label>
              </div>

              <!-- Same as Descent -->
              <div class="mt-4">
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" v-model="ascentSameAsDescent" class="w-4 h-4" />
                  <span style="color: #96EEF2">Same as Descent</span>
                </label>
              </div>

            </div>
          </div>

          <!-- Ascent Light -->
          <div class="mb-6">
            <div class="flex items-center justify-between mb-4">
              <h3 class="text-white flex items-center gap-2" style="font-weight: 500">
                <Lightbulb class="w-4 h-4" style="color: #41B9C3" />
                Light
              </h3>
              <label class="relative inline-flex items-center cursor-pointer">
                <input type="checkbox" v-model="ascentLightOn" class="sr-only peer" />
                <div class="w-11 h-6 rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all" :style="{ backgroundColor: ascentLightOn ? '#41B9C3' : 'rgba(65, 185, 195, 0.3)' }"></div>
                <span class="ml-3 text-sm" style="color: #96EEF2">{{ ascentLightOn ? 'On' : 'Off' }}</span>
              </label>
            </div>

            <div v-if="ascentLightOn" class="pl-6">
              <div v-if="ascentCameraOn && (ascentCameraType === 'timelapse' || ascentCameraType === 'video-interval')" class="mb-4 p-4 rounded-lg" style="background-color: rgba(65, 185, 195, 0.1); border: 1px solid rgba(65, 185, 195, 0.3)">
                <p class="text-sm" style="color: #96EEF2">
                  You have {{ ascentCameraType === 'timelapse' ? 'Timelapse Images' : 'Interval Video' }} selected. Light will automatically {{ ascentCameraType === 'timelapse' ? 'strobe to match camera frequency' : 'turn on to match camera frequency' }}.
                </p>
              </div>
              <div v-else class="mb-4">
                <label class="block mb-2 text-sm" style="color: #96EEF2">Light Mode</label>
                <div class="flex gap-4">
                  <label class="flex items-center gap-2 cursor-pointer">
                    <input type="radio" value="continuous" v-model="ascentLightMode" class="w-4 h-4" />
                    <span style="color: #96EEF2">Continuous Light</span>
                  </label>
                  <label class="flex items-center gap-2 cursor-not-allowed opacity-40">
                    <input type="radio" value="interval" v-model="ascentLightMode" disabled class="w-4 h-4" />
                    <span style="color: #96EEF2">Interval Light</span>
                  </label>
                </div>
              </div>

              <div v-if="ascentLightMode === 'interval' && !(ascentCameraOn && (ascentCameraType === 'timelapse' || ascentCameraType === 'video-interval'))" class="mb-4 p-4 rounded-lg space-y-4" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
                <h4 class="text-sm" style="color: #96EEF2">Interval Settings</h4>
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">Light On for</label>
                  <div class="flex gap-2">
                    <input type="number" v-model="ascentLightOnNumber" :disabled="ascentMatchCameraInterval" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle + '; opacity: ' + (ascentMatchCameraInterval ? '0.5' : '1')" min="1" />
                    <select v-model="ascentLightOnUnit" :disabled="ascentMatchCameraInterval" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle + '; opacity: ' + (ascentMatchCameraInterval ? '0.5' : '1')">
                      <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                    </select>
                  </div>
                </div>
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">Light Off for</label>
                  <div class="flex gap-2">
                    <input type="number" v-model="ascentLightOffNumber" :disabled="ascentMatchCameraInterval" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle + '; opacity: ' + (ascentMatchCameraInterval ? '0.5' : '1')" min="1" />
                    <select v-model="ascentLightOffUnit" :disabled="ascentMatchCameraInterval" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle + '; opacity: ' + (ascentMatchCameraInterval ? '0.5' : '1')">
                      <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                    </select>
                  </div>
                </div>
              </div>

              <label class="block mb-2 text-sm" style="color: #96EEF2">Light Brightness</label>
              <input type="range" min="0" max="100" :value="ascentLightBrightness" @input="handleBrightnessChange(Number(($event.target as HTMLInputElement).value), 'ascent')" class="w-full" />
              <div class="flex justify-between text-sm mt-1" style="color: #96EEF2">
                <span>0%</span><span>{{ ascentLightBrightness }}%</span><span>100%</span>
              </div>

              <div class="mt-4">
                <label class="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" v-model="ascentSameAsDescent" class="w-4 h-4" />
                  <span style="color: #96EEF2">Same as Descent</span>
                </label>
              </div>
            </div>
          </div>

          <!-- Ascent Data -->
          <div class="mb-6">
            <div class="mb-4">
              <h3 class="text-white flex items-center gap-2" style="font-weight: 500">
                <DatabaseIcon class="w-4 h-4" style="color: #41B9C3" />
                Data
              </h3>
            </div>
            <div class="pl-6">
              <p class="text-sm" style="color: rgba(150, 238, 242, 0.7)">Data collection is always on at the default sampling rate for each sensor. For sensor calibration, go to the Sensors page.</p>
            </div>
          </div>
        </template>
      </div>

      <!-- ==================== RECOVERY SECTION ==================== -->
      <div class="mb-6 p-6 rounded-lg" :style="phaseStyle">
        <h2 class="text-white text-xl mb-2 flex items-center gap-2">
          <Radio class="w-5 h-5" style="color: #96EEF2" />
          Recovery
        </h2>
        <p class="text-sm mb-6" style="color: rgba(150, 238, 242, 0.7)">
          Recovery settings for location, notifications, and visual signals are required and cannot be modified or deactivated.
        </p>
        <div class="space-y-3 text-sm" style="color: #96EEF2">
          <div class="flex gap-2"><span>•</span><span>Visual Signaling: The mast light LED ring will activate upon surfacing.</span></div>
          <div class="flex gap-2"><span>•</span><span>Surface Position Updates: Notifications will send every 15 minutes.</span></div>
          <div class="flex gap-2"><span>•</span><span>LoRa tracking is automatically activated.</span></div>
          <div class="flex gap-2"><span>•</span><span>Iridium tracking is automatically activated if Iridium is connected and active.</span></div>
        </div>
      </div>

      <!-- Battery Planning -->
      <div class="mt-6">
        <button @click="showBatteryPlanning = !showBatteryPlanning" class="w-full flex items-center justify-between p-4 rounded-lg transition-all" :class="{ 'battery-warning-pulse': batteryData.batteryUsagePercent > 80 }" :style="{ backgroundColor: batteryData.batteryUsagePercent > 80 ? 'rgba(221, 44, 29, 0.25)' : 'rgba(14, 36, 70, 0.5)', border: batteryData.batteryUsagePercent > 80 ? '1px solid rgba(221, 44, 29, 0.6)' : '1px solid rgba(65, 185, 195, 0.3)' }">
          <div class="flex items-center gap-3">
            <AlertTriangle v-if="batteryData.batteryUsagePercent > 80" class="w-6 h-6" style="color: #DD2C1D" />
            <Battery v-else class="w-6 h-6" style="color: #41B9C3" />
            <span class="text-white text-xl">Battery Planning</span>
          </div>
          <ChevronUp v-if="showBatteryPlanning" class="w-6 h-6" style="color: #96EEF2" />
          <ChevronDown v-else class="w-6 h-6" style="color: #96EEF2" />
        </button>

        <div v-if="showBatteryPlanning" class="mt-4 p-6 rounded-lg" :style="phaseStyle">
          <div class="mb-6">
            <label class="block mb-2 text-sm" style="color: #96EEF2">Estimated Dive Depth (m)</label>
            <input type="number" v-model="estimatedDepth" placeholder="Enter estimated depth" class="w-full px-4 py-3 text-white rounded-lg focus:outline-none" :style="inputStyle" />
            <p class="mt-2 text-xs" style="color: rgba(150, 238, 242, 0.6)">Enter the estimated maximum depth for this dive to help calculate battery requirements.</p>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
            <div class="p-4 rounded-lg" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
              <div class="flex items-center justify-between mb-2">
                <span style="color: #96EEF2">Average Power Draw</span>
                <span class="text-white text-xl">{{ batteryData.totalPower.toFixed(1) }} W</span>
              </div>
            </div>
            <div class="p-4 rounded-lg" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
              <div class="flex items-center justify-between mb-2">
                <span style="color: #96EEF2">Total Power Consumed</span>
                <span class="text-white text-xl">{{ batteryData.energyWh.toFixed(1) }} Wh</span>
              </div>
            </div>
            <div class="p-4 rounded-lg" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
              <div class="flex items-center justify-between mb-2">
                <span style="color: #96EEF2">Estimated Battery Life</span>
                <span class="text-white text-xl">{{ batteryData.batteryLife.toFixed(1) }}h</span>
              </div>
            </div>
          </div>

          <div class="p-4 rounded-lg" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
            <div class="flex items-center justify-between mb-3">
              <span style="color: #96EEF2">Battery Usage for Dive</span>
              <span class="text-white text-xl">{{ batteryData.batteryUsagePercent.toFixed(0) }}%</span>
            </div>
            <div class="w-full bg-gray-700 rounded-full h-4 overflow-hidden">
              <div class="h-full rounded-full transition-all" :style="{ width: `${batteryData.batteryUsagePercent}%`, background: batteryData.batteryUsagePercent > 80 ? 'linear-gradient(90deg, #DD2C1D 0%, #FF9937 100%)' : batteryData.batteryUsagePercent > 50 ? 'linear-gradient(90deg, #FF9937 0%, #FCD869 100%)' : 'linear-gradient(90deg, #41B9C3 0%, #96EEF2 100%)' }"></div>
            </div>

            <!-- Surface recovery time on remaining energy -->
            <div class="flex items-center justify-between mt-3 pt-3 text-sm" style="border-top: 1px solid rgba(65, 185, 195, 0.2)">
              <span style="color: #96EEF2">Surface recovery time</span>
              <span class="text-white">
                {{ batteryData.estimate.recoveryHours.toFixed(1) }} h
                <span style="color: rgba(150, 238, 242, 0.6)">({{ batteryData.estimate.remainingAfterDiveWh.toFixed(0) }} Wh left ÷ {{ batteryData.estimate.recoveryBaseW.toFixed(1) }} W base)</span>
              </span>
            </div>

            <!-- Breakdown dropdown -->
            <button
              type="button"
              @click="showBatteryBreakdown = !showBatteryBreakdown"
              class="mt-3 flex items-center gap-1 text-xs"
              style="color: #96EEF2"
            >
              <ChevronDown v-if="!showBatteryBreakdown" class="w-4 h-4" />
              <ChevronUp v-else class="w-4 h-4" />
              {{ showBatteryBreakdown ? 'Hide' : 'Show' }} calculation &amp; component breakdown
            </button>

            <div v-if="showBatteryBreakdown" class="mt-3 text-xs" style="color: #96EEF2">
              <p class="mb-2" style="color: rgba(150, 238, 242, 0.75)">
                Energy per phase = (Base {{ POWER.BASE_W }} W + LED×brightness×duty + Camera {{ POWER.CAMERA_RECORDING_W }} W×duty) × phase&nbsp;hours.
                Usage % = total energy ÷ {{ POWER.BATTERY_CAPACITY_WH.toFixed(0) }} Wh pack.
                Durations: descent = depth ÷ 1 m/s, bottom = release-weight time, ascent = {{ POWER.ASCENT_BURN_MINUTES }} min burn + depth ÷ 1 m/s.
              </p>
              <div class="overflow-x-auto">
                <table class="w-full text-left" style="border-collapse: collapse">
                  <thead>
                    <tr style="color: rgba(150, 238, 242, 0.9)">
                      <th class="py-1 pr-3">Phase</th>
                      <th class="py-1 pr-3">Dur (h)</th>
                      <th class="py-1 pr-3">Base</th>
                      <th class="py-1 pr-3">Lights</th>
                      <th class="py-1 pr-3">Camera</th>
                      <th class="py-1">Total (Wh)</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="phase in batteryData.estimate.phases"
                      :key="phase.name"
                      style="border-top: 1px solid rgba(65, 185, 195, 0.2)"
                    >
                      <td class="py-1 pr-3 text-white">
                        {{ phase.name }}
                        <span v-if="phase.lightWh > 0" style="color: rgba(150, 238, 242, 0.6)">
                          ({{ phase.brightnessPct }}% · {{ Math.round(phase.lightDuty * 100) }}% on)
                        </span>
                      </td>
                      <td class="py-1 pr-3">{{ phase.hours.toFixed(2) }}</td>
                      <td class="py-1 pr-3">{{ phase.baseWh.toFixed(1) }}</td>
                      <td class="py-1 pr-3">{{ phase.lightWh.toFixed(1) }}</td>
                      <td class="py-1 pr-3">{{ phase.cameraWh.toFixed(1) }}</td>
                      <td class="py-1 text-white">{{ phase.totalWh.toFixed(1) }}</td>
                    </tr>
                    <tr style="border-top: 1px solid rgba(65, 185, 195, 0.4)">
                      <td class="py-1 pr-3 text-white">Total</td>
                      <td class="py-1 pr-3 text-white">{{ batteryData.estimate.totalHours.toFixed(2) }}</td>
                      <td class="py-1 pr-3 text-white">{{ batteryData.estimate.baseWh.toFixed(1) }}</td>
                      <td class="py-1 pr-3 text-white">{{ batteryData.estimate.lightWh.toFixed(1) }}</td>
                      <td class="py-1 pr-3 text-white">{{ batteryData.estimate.cameraWh.toFixed(1) }}</td>
                      <td class="py-1 text-white">{{ batteryData.estimate.energyWh.toFixed(1) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div v-if="batteryData.batteryUsagePercent > 80" class="mt-4 p-4 rounded-lg" style="background-color: rgba(221, 44, 29, 0.1); border: 1px solid rgba(221, 44, 29, 0.3)">
            <div class="flex items-start gap-3">
              <AlertTriangle class="w-5 h-5 flex-shrink-0 mt-0.5" style="color: #DD2C1D" />
              <div>
                <p class="mb-2" style="color: #DD2C1D">Battery Warning:</p>
                <p class="text-sm" style="color: #FF9937">Dive configuration may exceed battery capacity. Consider changing settings to reduce power consumption or dive duration.</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Data Usage Planning -->
      <div class="mt-6">
        <button @click="showDataPlanning = !showDataPlanning" class="w-full flex items-center justify-between p-4 rounded-lg transition-all" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)">
          <div class="flex items-center gap-3">
            <DatabaseIcon class="w-6 h-6" style="color: #41B9C3" />
            <span class="text-white text-xl">Data Usage</span>
            <span class="text-sm px-2 py-0.5 rounded" style="color: #96EEF2; background-color: rgba(65, 185, 195, 0.15)">~{{ dataUsage.totalLabel }}</span>
          </div>
          <ChevronUp v-if="showDataPlanning" class="w-6 h-6" style="color: #96EEF2" />
          <ChevronDown v-else class="w-6 h-6" style="color: #96EEF2" />
        </button>

        <div v-if="showDataPlanning" class="mt-4 p-6 rounded-lg" :style="phaseStyle">
          <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
            <div class="p-4 rounded-lg" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
              <div class="flex items-center justify-between mb-2">
                <span style="color: #96EEF2">Estimated Data</span>
                <span class="text-white text-xl">{{ dataUsage.totalLabel }}</span>
              </div>
            </div>
            <div class="p-4 rounded-lg" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
              <div class="flex items-center justify-between mb-2">
                <span style="color: #96EEF2">Video Bitrate</span>
                <span class="text-white text-xl">{{ dataUsage.bitrateMbps.toFixed(dataUsage.bitrateMbps < 10 ? 1 : 0) }} Mbps</span>
              </div>
            </div>
            <div class="p-4 rounded-lg" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
              <div class="flex items-center justify-between mb-2">
                <span style="color: #96EEF2">Dive Duration</span>
                <span class="text-white text-xl">{{ dataUsage.estimate.totalHours.toFixed(1) }}h</span>
              </div>
            </div>
          </div>

          <p class="text-xs mb-3" style="color: rgba(150, 238, 242, 0.6)">
            Set the estimated dive depth in Battery Planning above to include descent/ascent recording time.
            Video storage scales with the global bitrate and each phase's recording duty; timelapse uses the still count.
          </p>

          <button
            type="button"
            @click="showDataBreakdown = !showDataBreakdown"
            class="flex items-center gap-1 text-xs"
            style="color: #96EEF2"
          >
            <ChevronDown v-if="!showDataBreakdown" class="w-4 h-4" />
            <ChevronUp v-else class="w-4 h-4" />
            {{ showDataBreakdown ? 'Hide' : 'Show' }} per-phase breakdown
          </button>

          <div v-if="showDataBreakdown" class="mt-3 text-xs" style="color: #96EEF2">
            <p class="mb-2" style="color: rgba(150, 238, 242, 0.75)">
              Video bytes = bitrate × recording time (codec-independent at a fixed bitrate).
              Timelapse ≈ still count × ~{{ STILL_BYTES_PER_PIXEL }} bytes/pixel at the capture resolution.
              Durations: descent = depth ÷ 1 m/s, bottom = release-weight time, ascent = {{ POWER.ASCENT_BURN_MINUTES }} min burn + depth ÷ 1 m/s.
            </p>
            <div class="overflow-x-auto">
              <table class="w-full text-left" style="border-collapse: collapse">
                <thead>
                  <tr style="color: rgba(150, 238, 242, 0.9)">
                    <th class="py-1 pr-3">Phase</th>
                    <th class="py-1 pr-3">Dur (h)</th>
                    <th class="py-1 pr-3">Mode</th>
                    <th class="py-1 pr-3">Recorded</th>
                    <th class="py-1">Data</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="phase in dataUsage.estimate.phases"
                    :key="phase.name"
                    style="border-top: 1px solid rgba(65, 185, 195, 0.2)"
                  >
                    <td class="py-1 pr-3 text-white">{{ phase.name }}</td>
                    <td class="py-1 pr-3">{{ phase.hours.toFixed(2) }}</td>
                    <td class="py-1 pr-3">
                      <span v-if="phase.mode === 'off'" style="color: rgba(150, 238, 242, 0.5)">camera off</span>
                      <span v-else-if="phase.mode === 'timelapse'">timelapse</span>
                      <span v-else-if="phase.mode === 'video-interval'">interval video</span>
                      <span v-else>continuous</span>
                    </td>
                    <td class="py-1 pr-3">
                      <span v-if="phase.mode === 'timelapse'">{{ phase.stillCount }} stills</span>
                      <span v-else-if="phase.mode !== 'off'">{{ (phase.recordSeconds / 60).toFixed(0) }} min</span>
                      <span v-else style="color: rgba(150, 238, 242, 0.5)">—</span>
                    </td>
                    <td class="py-1 text-white">{{ formatBytes(phase.bytes) }}</td>
                  </tr>
                  <tr style="border-top: 1px solid rgba(65, 185, 195, 0.4)">
                    <td class="py-1 pr-3 text-white">Total</td>
                    <td class="py-1 pr-3 text-white">{{ dataUsage.estimate.totalHours.toFixed(2) }}</td>
                    <td class="py-1 pr-3"></td>
                    <td class="py-1 pr-3"></td>
                    <td class="py-1 text-white">{{ dataUsage.totalLabel }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      <!-- Save Configuration Button -->
      <div class="mt-6 mb-6">
        <div v-if="configurationSaveError" class="mb-4 rounded-lg p-4 flex items-start justify-between gap-3" style="background-color: rgba(221, 44, 29, 0.15); border: 1px solid rgba(221, 44, 29, 0.4)">
          <p class="text-sm flex-1" style="color: #FF9937">{{ configurationSaveError }}</p>
          <button type="button" class="text-sm shrink-0 px-2 py-1 rounded" style="color: #96EEF2; border: 1px solid rgba(150, 238, 242, 0.4)" @click="clearConfigurationSaveError">Dismiss</button>
        </div>
        <div class="flex flex-col sm:flex-row gap-3">
          <button @click="handleOpenSaveModal" class="flex-1 px-6 py-4 text-white rounded-lg transition-all hover:opacity-90 flex items-center justify-center gap-2" style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)">
            <Save class="w-5 h-5" />
            {{ selectedConfiguration && selectedConfiguration !== 'New Configuration' ? 'Save Configuration' : 'Save New Configuration' }}
          </button>
          <button v-if="selectedConfiguration && selectedConfiguration !== 'New Configuration'" @click="clearConfigurationSaveError(); configurationName = ''; showSaveModal = true" class="px-6 py-4 text-white rounded-lg transition-all hover:opacity-90 flex items-center justify-center gap-2" style="background-color: rgba(65, 185, 195, 0.3); border: 1px solid #41B9C3">
            <Copy class="w-5 h-5" />
            Save As...
          </button>
        </div>
      </div>
    </div>

    <!-- Save Configuration Modal -->
    <Teleport to="body">
      <div v-if="showSaveModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
        <div class="w-full max-w-md rounded-xl p-6" style="background-color: #0E2446; border: 2px solid #41B9C3">
          <div class="flex items-center justify-between mb-4">
            <h2 class="text-white text-xl">Save Configuration</h2>
            <button @click="showSaveModal = false" class="text-white hover:opacity-80 transition-opacity">
              <X class="w-5 h-5" />
            </button>
          </div>

          <template v-if="selectedConfiguration && selectedConfiguration !== 'New Configuration' && !configurationName">
            <div class="mb-6">
              <p class="text-white mb-4">Save changes to <span class="font-semibold" style="color: #96EEF2">{{ selectedConfiguration }}</span>?</p>
              <div class="rounded-lg p-3 mb-4" style="background-color: rgba(65, 185, 195, 0.1); border: 1px solid rgba(65, 185, 195, 0.3)">
                <p class="text-sm" style="color: #96EEF2"><strong>Overwrite:</strong> Replace the existing configuration with your updated settings.</p>
              </div>
              <div class="rounded-lg p-3" style="background-color: rgba(255, 153, 55, 0.1); border: 1px solid rgba(255, 153, 55, 0.3)">
                <p class="text-sm" style="color: #FF9937"><strong>Save As New:</strong> Create a new configuration. The original will remain unchanged.</p>
              </div>
            </div>
            <div class="flex flex-col gap-3">
              <div class="flex gap-3">
                <button @click="showSaveModal = false" class="flex-1 px-4 py-3 rounded-lg transition-all" style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid rgba(65, 185, 195, 0.3); color: #96EEF2">Cancel</button>
                <button @click="handleOverwriteSave" class="flex-1 px-4 py-3 text-white rounded-lg transition-all hover:opacity-90" style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)">
                  <Save class="w-4 h-4 inline mr-2" />Overwrite
                </button>
              </div>
              <button @click="handleSaveAsNew" class="w-full px-4 py-3 text-white rounded-lg transition-all hover:opacity-90" style="background-color: #FF9937">
                <Copy class="w-4 h-4 inline mr-2" />Save As New
              </button>
            </div>
          </template>

          <template v-else>
            <div class="mb-6">
              <label class="block mb-2 text-sm" style="color: #96EEF2">Configuration Name</label>
              <input type="text" v-model="configurationName" @keypress.enter="handleSaveConfiguration" placeholder="Enter configuration name" class="w-full px-4 py-3 text-white rounded-lg focus:outline-none" :style="inputStyle" autofocus />
              <p v-if="selectedConfiguration && selectedConfiguration !== 'New Configuration'" class="text-sm mt-2" style="color: #96EEF2; opacity: 0.8">
                Saving as a new configuration. Original will remain unchanged.
              </p>
            </div>
            <div class="flex gap-3">
              <button @click="showSaveModal = false" class="flex-1 px-4 py-3 rounded-lg transition-all" style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid rgba(65, 185, 195, 0.3); color: #96EEF2">Cancel</button>
              <button @click="handleSaveConfiguration" :disabled="!configurationName.trim()" class="flex-1 px-4 py-3 text-white rounded-lg transition-all hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed" style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)">
                <Save class="w-4 h-4 inline mr-2" />Save
              </button>
            </div>
          </template>
        </div>
      </div>
    </Teleport>

    <!-- Delete Configuration Modal -->
    <Teleport to="body">
      <div v-if="showDeleteModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
        <div class="w-full max-w-md rounded-xl p-6" style="background-color: #0E2446; border: 2px solid #DD2C1D">
          <div class="flex items-center justify-between mb-4">
            <h2 class="text-white text-xl flex items-center gap-2">
              <Trash2 class="w-5 h-5" style="color: #FF6B5E" />
              Delete Configuration
            </h2>
            <button @click="cancelDeleteConfiguration" class="text-white hover:opacity-80 transition-opacity">
              <X class="w-5 h-5" />
            </button>
          </div>
          <p class="text-white mb-4">
            Permanently delete <span class="font-semibold" style="color: #96EEF2">{{ selectedConfiguration }}</span>? This cannot be undone.
          </p>
          <p v-if="deleteError" class="text-sm mb-4 rounded-lg p-3" style="color: #FF9937; background-color: rgba(221, 44, 29, 0.15); border: 1px solid rgba(221, 44, 29, 0.4)">{{ deleteError }}</p>
          <div class="flex gap-3">
            <button @click="cancelDeleteConfiguration" :disabled="deleting" class="flex-1 px-4 py-3 rounded-lg transition-all disabled:opacity-50" style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid rgba(65, 185, 195, 0.3); color: #96EEF2">Cancel</button>
            <button @click="confirmDeleteConfiguration" :disabled="deleting" class="flex-1 px-4 py-3 text-white rounded-lg transition-all hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed" style="background-color: #DD2C1D">
              <Trash2 class="w-4 h-4 inline mr-2" />{{ deleting ? 'Deleting…' : 'Delete' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Navigation Warning Modal -->
    <Teleport to="body">
      <div v-if="showNavigationWarning" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
        <div class="w-full max-w-lg rounded-xl p-6" style="background-color: #0E2446; border: 2px solid #FF9937">
          <div class="flex items-start gap-4 mb-6">
            <AlertTriangle class="w-6 h-6 flex-shrink-0 mt-1" style="color: #FF9937" />
            <div>
              <h2 class="text-white text-xl mb-2" style="font-family: Montserrat, sans-serif">Unsaved Changes</h2>
              <p class="text-white text-sm opacity-90">You have unsaved changes to your configuration. Would you like to save before switching?</p>
            </div>
          </div>
          <div class="flex flex-col sm:flex-row gap-3">
            <button @click="handleCancelNavigation" class="flex-1 px-4 py-3 rounded-lg transition-all" style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid rgba(65, 185, 195, 0.3); color: #96EEF2">Cancel</button>
            <button @click="handleDiscardChanges" class="flex-1 px-4 py-3 rounded-lg transition-all hover:opacity-90" style="background-color: rgba(221, 44, 29, 0.2); border: 1px solid rgba(221, 44, 29, 0.4); color: #DD2C1D">Discard Changes</button>
            <button @click="showNavigationWarning = false; handleOpenSaveModal()" class="flex-1 px-4 py-3 text-white rounded-lg transition-all hover:opacity-90" style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)">Save Configuration</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Brightness Warning Banner -->
    <Teleport to="body">
      <div v-if="showBrightnessWarning" class="fixed top-4 left-1/2 transform -translate-x-1/2 z-50 w-full max-w-2xl px-4">
        <div class="rounded-lg p-4 shadow-xl" style="background-color: #0E2446; border: 2px solid #DD2C1D">
          <div class="flex items-start gap-3">
            <AlertTriangle class="w-5 h-5 flex-shrink-0 mt-0.5" style="color: #DD2C1D" />
            <div class="flex-1">
              <h3 class="text-white font-semibold mb-1">Low Brightness Warning</h3>
              <p class="text-white text-sm opacity-90">Setting the light brightness below 50% may result in poor image quality and reduced visibility.</p>
            </div>
            <div class="flex gap-2 ml-2">
              <button @click="cancelBrightnessChange" class="px-3 py-1.5 text-sm text-white rounded transition-all hover:opacity-80" style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid rgba(65, 185, 195, 0.4)">Cancel</button>
              <button @click="confirmBrightnessChange" class="px-3 py-1.5 text-sm text-white rounded transition-all hover:opacity-90" style="background-color: #DD2C1D">Continue</button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Unsaved Changes Warning Banner -->
    <Teleport to="body">
      <div v-if="hasUnsavedChanges" class="fixed bottom-0 left-0 right-0 p-3 md:p-4 border-t" style="background-color: rgba(255, 153, 55, 0.95); backdrop-filter: blur(8px); border-color: #FF9937; z-index: 60">
        <div class="max-w-6xl mx-auto flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div class="flex items-center gap-2 sm:gap-3 flex-1 min-w-0">
            <AlertTriangle class="w-4 h-4 sm:w-5 sm:h-5 text-white flex-shrink-0" />
            <p class="text-white font-medium text-sm sm:text-base">
              <span class="hidden sm:inline">You have unsaved changes. Please save your configuration before navigating away.</span>
              <span class="sm:hidden">Unsaved changes. Please save before leaving.</span>
            </p>
          </div>
          <button @click="handleOpenSaveModal" class="w-full sm:w-auto px-4 sm:px-6 py-2 text-white text-sm sm:text-base rounded-lg transition-all hover:opacity-90 whitespace-nowrap" style="background-color: #0E2446; border: 1px solid #41B9C3">
            Save Configuration
          </button>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style>
.battery-warning-pulse {
  animation: batteryPulse 2s ease-in-out infinite;
}

@keyframes batteryPulse {
  0%, 100% {
    box-shadow: 0 0 8px 2px rgba(221, 44, 29, 0.3);
    background-color: rgba(221, 44, 29, 0.15);
  }
  50% {
    box-shadow: 0 0 24px 6px rgba(221, 44, 29, 0.7);
    background-color: rgba(221, 44, 29, 0.35);
  }
}
</style>
