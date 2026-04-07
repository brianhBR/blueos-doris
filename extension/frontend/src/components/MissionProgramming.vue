<script setup lang="ts">
import { ref, watch, nextTick, onMounted, onUnmounted, computed } from 'vue'
import { Settings, Save, Copy, AlertTriangle, ChevronDown, ChevronUp, Camera as CameraIcon, Lightbulb, Database as DatabaseIcon, Battery, ArrowDown, Anchor, ArrowUp, Radio, X } from 'lucide-vue-next'
import type { Screen } from '../types'
import { useConfigurations } from '../composables/useApi'
import type { DeploymentConfiguration } from '../composables/useApi'

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
const showSaveModal = ref(false)
const configurationName = ref('')
const hasUnsavedChanges = ref(false)
let suppressUnsavedTracking = false
const showNavigationWarning = ref(false)
const pendingConfigurationChange = ref('')
const {
  configurations: savedConfigSummaries,
  fetchConfigurations,
  loadConfiguration,
  saveConfiguration,
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
const descentResolution = ref('4K')
const descentImageType = ref('High-Rez JPG')
const descentFileFormat = ref('JPEG')
const descentVideoFileFormat = ref('.MP4')
const descentFrameRate = ref(30)
const descentCaptureFrequency = ref(10)
const descentCaptureFrequencyUnit = ref('seconds')
const descentFocus = ref('auto')
const descentSleepTimerNumber = ref('')
const descentSleepTimerUnit = ref('hours')
const descentSleepTimerEnabled = ref(false)
const descentAdvancedOpen = ref(false)
const descentISO = ref('auto')
const descentWhiteBalance = ref('auto')
const descentExposure = ref('0')
const descentSharpness = ref('medium')
const descentLightOn = ref(false)
const descentLightMode = ref<'continuous' | 'interval'>('continuous')
const descentLightOnNumber = ref('10')
const descentLightOnUnit = ref('seconds')
const descentLightOffNumber = ref('5')
const descentLightOffUnit = ref('seconds')
const descentLightBrightness = ref(75)
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
const bottomResolution = ref('4K')
const bottomImageType = ref('High-Rez JPG')
const bottomFileFormat = ref('JPEG')
const bottomVideoFileFormat = ref('.MP4')
const bottomFrameRate = ref(30)
const bottomCaptureFrequency = ref(10)
const bottomCaptureFrequencyUnit = ref('seconds')
const bottomFocus = ref('auto')
const bottomSleepTimerNumber = ref('')
const bottomSleepTimerUnit = ref('hours')
const bottomSleepTimerEnabled = ref(false)
const bottomAdvancedOpen = ref(false)
const bottomISO = ref('auto')
const bottomWhiteBalance = ref('auto')
const bottomExposure = ref('0')
const bottomSharpness = ref('medium')
const bottomLightOn = ref(true)
const bottomLightDelayNumber = ref('30')
const bottomLightDelayUnit = ref('seconds')
const bottomLightMode = ref<'continuous' | 'interval'>('continuous')
const bottomLightOnNumber = ref('10')
const bottomLightOnUnit = ref('seconds')
const bottomLightOffNumber = ref('5')
const bottomLightOffUnit = ref('seconds')
const bottomLightBrightness = ref(75)
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
const ascentResolution = ref('4K')
const ascentImageType = ref('High-Rez JPG')
const ascentFileFormat = ref('JPEG')
const ascentVideoFileFormat = ref('.MP4')
const ascentFrameRate = ref(30)
const ascentCaptureFrequency = ref(10)
const ascentCaptureFrequencyUnit = ref('seconds')
const ascentFocus = ref('auto')
const ascentSleepTimerNumber = ref('')
const ascentSleepTimerUnit = ref('hours')
const ascentSleepTimerEnabled = ref(false)
const ascentAdvancedOpen = ref(false)
const ascentISO = ref('auto')
const ascentWhiteBalance = ref('auto')
const ascentExposure = ref('0')
const ascentSharpness = ref('medium')
const ascentLightOn = ref(false)
const ascentLightMode = ref<'continuous' | 'interval'>('continuous')
const ascentLightOnNumber = ref('10')
const ascentLightOnUnit = ref('seconds')
const ascentLightOffNumber = ref('5')
const ascentLightOffUnit = ref('seconds')
const ascentLightBrightness = ref(75)
const ascentMatchCameraInterval = ref(false)

// Recovery settings
const activateMastLight = ref(false)
const updateFrequency = ref('5min')
const useIridium = ref(false)
const useLoRA = ref(false)

const batteryData = computed(() => {
  // -- Descent / ascent parameters --
  const descentRate_m_per_s = 1          // descent speed in meters per second
  const descentRate_m_per_min = descentRate_m_per_s * 60

  // -- Power consumption (watts) --
  const powerRPi5_W = 15                // Raspberry Pi 5 single-board computer
  const powerRadCAM_W = 5               // RadCAM camera module
  const powerPerLumen_W = 15            // each lumen light module
  const lumenCount = 2                  // number of lumen lights installed

  // -- Battery --
  const batteryCapacity_Wh = 266        // onboard battery capacity in watt-hours

  // -- Dive duration from depth + release weight settings --
  const depth = parseFloat(estimatedDepth.value) || 0
  const descentTimeHours = depth / descentRate_m_per_min / 60
  const ascentTimeHours = depth / descentRate_m_per_min / 60

  let diveDuration = 0
  if (props.releaseWeightBy === 'elapsed') {
    const num = Number(releaseWeightElapsedNumber.value) || 0
    const unit = releaseWeightElapsedUnit.value
    diveDuration = unit === 'hours' ? num : unit === 'minutes' ? num / 60 : num / 3600
  } else {
    diveDuration = descentTimeHours + ascentTimeHours
  }

  // -- Derived values --
  const totalPower = powerRPi5_W + powerRadCAM_W + (powerPerLumen_W * lumenCount)
  const batteryLife = totalPower > 0 ? batteryCapacity_Wh / totalPower : 0
  const batteryUsagePercent = batteryLife > 0
    ? Math.min((diveDuration / batteryLife) * 100, 100)
    : 0
  return { totalPower, batteryLife, batteryUsagePercent, diveDuration }
})

const descentCaptureFrequencyTooLow = computed(() => {
  const totalHours = descentCaptureFrequencyUnit.value === 'hours'
    ? descentCaptureFrequency.value
    : descentCaptureFrequencyUnit.value === 'minutes'
    ? descentCaptureFrequency.value / 60
    : descentCaptureFrequency.value / 3600
  return totalHours > 1
})

const releaseWeightWarning = computed(() => {
  const totalMinutes = releaseWeightElapsedUnit.value === 'hours'
    ? Number(releaseWeightElapsedNumber.value) * 60
    : releaseWeightElapsedUnit.value === 'minutes'
    ? Number(releaseWeightElapsedNumber.value)
    : Number(releaseWeightElapsedNumber.value) / 60
  if (totalMinutes < 20) return { show: true, severity: 'warning' as const, title: 'Release Time May Be Short', message: 'Release time is under 20 minutes. This may not allow enough dive duration, but will be used as configured.' }
  if (totalMinutes > 1200) return { show: true, severity: 'error' as const, title: 'Release Time Too Long', message: 'Release time exceeds 20 hours. Consider if this extended duration is necessary for mission objectives.' }
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

watch([diveName, descentCameraOn, descentCameraType, descentResolution, descentCaptureFrequency,
  descentLightOn, descentLightMode, descentLightBrightness, bottomCameraOn, bottomCameraType, bottomResolution,
  bottomCaptureFrequency, bottomLightOn, bottomLightMode, bottomLightBrightness, ascentCameraOn, ascentCameraType,
  ascentResolution, ascentCaptureFrequency, ascentLightOn, ascentLightMode, ascentLightBrightness,
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
  descentResolution.value = '4K'
  descentImageType.value = 'High-Rez JPG'
  descentFileFormat.value = 'JPEG'
  descentVideoFileFormat.value = '.MP4'
  descentFrameRate.value = 30
  descentCaptureFrequency.value = 10
  descentCaptureFrequencyUnit.value = 'seconds'
  descentFocus.value = 'auto'
  descentSleepTimerEnabled.value = false
  descentSleepTimerNumber.value = ''
  descentSleepTimerUnit.value = 'hours'
  descentAdvancedOpen.value = false
  descentISO.value = 'auto'
  descentWhiteBalance.value = 'auto'
  descentExposure.value = '0'
  descentSharpness.value = 'medium'
  descentLightOn.value = false
  descentLightMode.value = 'continuous'
  descentLightOnNumber.value = '10'
  descentLightOnUnit.value = 'seconds'
  descentLightOffNumber.value = '5'
  descentLightOffUnit.value = 'seconds'
  descentLightBrightness.value = 75
  descentMatchCameraInterval.value = false
  bottomCameraOn.value = true
  bottomCameraDelayNumber.value = '30'
  bottomCameraDelayUnit.value = 'seconds'
  bottomCameraType.value = 'continuous-video'
  bottomVideoRecordNumber.value = '10'
  bottomVideoRecordUnit.value = 'seconds'
  bottomVideoPauseNumber.value = '5'
  bottomVideoPauseUnit.value = 'seconds'
  bottomResolution.value = '4K'
  bottomImageType.value = 'High-Rez JPG'
  bottomFileFormat.value = 'JPEG'
  bottomVideoFileFormat.value = '.MP4'
  bottomFrameRate.value = 30
  bottomCaptureFrequency.value = 10
  bottomCaptureFrequencyUnit.value = 'seconds'
  bottomFocus.value = 'auto'
  bottomSleepTimerEnabled.value = false
  bottomSleepTimerNumber.value = ''
  bottomSleepTimerUnit.value = 'hours'
  bottomAdvancedOpen.value = false
  bottomISO.value = 'auto'
  bottomWhiteBalance.value = 'auto'
  bottomExposure.value = '0'
  bottomSharpness.value = 'medium'
  bottomLightOn.value = true
  bottomLightDelayNumber.value = '30'
  bottomLightDelayUnit.value = 'seconds'
  bottomLightMode.value = 'continuous'
  bottomLightOnNumber.value = '10'
  bottomLightOnUnit.value = 'seconds'
  bottomLightOffNumber.value = '5'
  bottomLightOffUnit.value = 'seconds'
  bottomLightBrightness.value = 75
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
  ascentResolution.value = '4K'
  ascentImageType.value = 'High-Rez JPG'
  ascentFileFormat.value = 'JPEG'
  ascentVideoFileFormat.value = '.MP4'
  ascentFrameRate.value = 30
  ascentCaptureFrequency.value = 10
  ascentCaptureFrequencyUnit.value = 'seconds'
  ascentFocus.value = 'auto'
  ascentSleepTimerEnabled.value = false
  ascentSleepTimerNumber.value = ''
  ascentSleepTimerUnit.value = 'hours'
  ascentAdvancedOpen.value = false
  ascentISO.value = 'auto'
  ascentWhiteBalance.value = 'auto'
  ascentExposure.value = '0'
  ascentSharpness.value = 'medium'
  ascentLightOn.value = false
  ascentLightMode.value = 'continuous'
  ascentLightOnNumber.value = '10'
  ascentLightOnUnit.value = 'seconds'
  ascentLightOffNumber.value = '5'
  ascentLightOffUnit.value = 'seconds'
  ascentLightBrightness.value = 75
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
        resolution: descentResolution.value,
        image_type: descentImageType.value,
        file_format: descentFileFormat.value,
        video_file_format: descentVideoFileFormat.value,
        frame_rate: safePositiveInt(descentFrameRate.value, 30),
        focus: descentFocus.value,
        iso: descentISO.value,
        white_balance: descentWhiteBalance.value,
        exposure: descentExposure.value,
        sharpness: descentSharpness.value,
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
    },
    bottom: {
      camera: {
        enabled: bottomCameraOn.value,
        camera_type: bottomCameraType.value,
        capture_frequency: safePositiveInt(bottomCaptureFrequency.value, 10),
        capture_frequency_unit: bottomCaptureFrequencyUnit.value as 'seconds' | 'minutes' | 'hours',
        video_record: tv(bottomVideoRecordNumber.value, bottomVideoRecordUnit.value),
        video_pause: tv(bottomVideoPauseNumber.value, bottomVideoPauseUnit.value),
        resolution: bottomResolution.value,
        image_type: bottomImageType.value,
        file_format: bottomFileFormat.value,
        video_file_format: bottomVideoFileFormat.value,
        frame_rate: safePositiveInt(bottomFrameRate.value, 30),
        focus: bottomFocus.value,
        iso: bottomISO.value,
        white_balance: bottomWhiteBalance.value,
        exposure: bottomExposure.value,
        sharpness: bottomSharpness.value,
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
        resolution: ascentResolution.value,
        image_type: ascentImageType.value,
        file_format: ascentFileFormat.value,
        video_file_format: ascentVideoFileFormat.value,
        frame_rate: safePositiveInt(ascentFrameRate.value, 30),
        focus: ascentFocus.value,
        iso: ascentISO.value,
        white_balance: ascentWhiteBalance.value,
        exposure: ascentExposure.value,
        sharpness: ascentSharpness.value,
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
  descentCameraType.value = cfg.descent.camera.camera_type
  descentCaptureFrequency.value = cfg.descent.camera.capture_frequency
  descentCaptureFrequencyUnit.value = cfg.descent.camera.capture_frequency_unit
  descentVideoRecordNumber.value = cfg.descent.camera.video_record.number
  descentVideoRecordUnit.value = cfg.descent.camera.video_record.unit
  descentVideoPauseNumber.value = cfg.descent.camera.video_pause.number
  descentVideoPauseUnit.value = cfg.descent.camera.video_pause.unit
  descentResolution.value = cfg.descent.camera.resolution
  descentImageType.value = cfg.descent.camera.image_type
  descentFileFormat.value = cfg.descent.camera.file_format
  descentVideoFileFormat.value = cfg.descent.camera.video_file_format
  descentFrameRate.value = cfg.descent.camera.frame_rate
  descentFocus.value = cfg.descent.camera.focus
  descentISO.value = cfg.descent.camera.iso
  descentWhiteBalance.value = cfg.descent.camera.white_balance
  descentExposure.value = cfg.descent.camera.exposure
  descentSharpness.value = cfg.descent.camera.sharpness
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
  bottomResolution.value = cfg.bottom.camera.resolution
  bottomImageType.value = cfg.bottom.camera.image_type
  bottomFileFormat.value = cfg.bottom.camera.file_format
  bottomVideoFileFormat.value = cfg.bottom.camera.video_file_format
  bottomFrameRate.value = cfg.bottom.camera.frame_rate
  bottomFocus.value = cfg.bottom.camera.focus
  bottomISO.value = cfg.bottom.camera.iso
  bottomWhiteBalance.value = cfg.bottom.camera.white_balance
  bottomExposure.value = cfg.bottom.camera.exposure
  bottomSharpness.value = cfg.bottom.camera.sharpness
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

  ascentSameAsDescent.value = cfg.ascent.same_as_descent
  ascentCameraOn.value = cfg.ascent.camera.enabled
  ascentCameraType.value = cfg.ascent.camera.camera_type
  ascentCaptureFrequency.value = cfg.ascent.camera.capture_frequency
  ascentCaptureFrequencyUnit.value = cfg.ascent.camera.capture_frequency_unit
  ascentVideoRecordNumber.value = cfg.ascent.camera.video_record.number
  ascentVideoRecordUnit.value = cfg.ascent.camera.video_record.unit
  ascentVideoPauseNumber.value = cfg.ascent.camera.video_pause.number
  ascentVideoPauseUnit.value = cfg.ascent.camera.video_pause.unit
  ascentResolution.value = cfg.ascent.camera.resolution
  ascentImageType.value = cfg.ascent.camera.image_type
  ascentFileFormat.value = cfg.ascent.camera.file_format
  ascentVideoFileFormat.value = cfg.ascent.camera.video_file_format
  ascentFrameRate.value = cfg.ascent.camera.frame_rate
  ascentFocus.value = cfg.ascent.camera.focus
  ascentISO.value = cfg.ascent.camera.iso
  ascentWhiteBalance.value = cfg.ascent.camera.white_balance
  ascentExposure.value = cfg.ascent.camera.exposure
  ascentSharpness.value = cfg.ascent.camera.sharpness
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

function handleBrightnessChange(value: number, phase: 'descent' | 'bottom' | 'ascent') {
  const currentBrightness = phase === 'descent' ? descentLightBrightness.value
    : phase === 'bottom' ? bottomLightBrightness.value
    : ascentLightBrightness.value
  if (value < 75 && currentBrightness >= 75) {
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

onMounted(() => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  fetchConfigurations()
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
        <select :value="selectedConfiguration" @change="handleConfigurationChange(($event.target as HTMLSelectElement).value)" class="w-full px-4 py-3 text-white rounded-lg focus:outline-none mb-3" :style="inputStyle">
          <option value="">-- Select Configuration --</option>
          <option value="New Configuration">New Configuration</option>
          <option v-for="(config, index) in savedConfigurations" :key="index" :value="config">{{ config }}</option>
        </select>
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

            <!-- Sleep Timer (disabled) -->
            <div class="mt-4 opacity-40">
              <label class="flex items-center gap-2 mb-2 text-sm cursor-not-allowed" style="color: #96EEF2">
                <input type="checkbox" disabled class="w-4 h-4 cursor-not-allowed" style="accent-color: #41B9C3" />
                Optional: Stop recording and go to sleep after elapsed time of:
              </label>
            </div>

            <!-- Camera Settings Toggle -->
            <button @click="descentAdvancedOpen = !descentAdvancedOpen" class="flex items-center gap-2 px-4 py-2 mt-2 rounded-lg transition-all hover:opacity-80" style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid rgba(65, 185, 195, 0.4); color: #96EEF2">
              <ChevronUp v-if="descentAdvancedOpen" class="w-5 h-5" />
              <ChevronDown v-else class="w-5 h-5" />
              <span class="font-medium">Camera Settings</span>
            </button>

            <div v-if="descentAdvancedOpen" class="p-4 rounded-lg space-y-4 opacity-50 pointer-events-none" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
              <template v-if="descentCameraType !== 'timelapse'">
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">Resolution</label>
                  <select disabled v-model="descentResolution" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                    <option value="4K">4K</option><option value="2.7K">2.7K</option><option value="1080p">1080p</option><option value="720p">720p</option>
                  </select>
                </div>
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">Frame Rate</label>
                  <select disabled v-model.number="descentFrameRate" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                    <option :value="24">24 fps</option><option :value="30">30 fps</option><option :value="60">60 fps</option>
                  </select>
                </div>
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">File Format</label>
                  <select disabled v-model="descentVideoFileFormat" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                    <option value=".MP4">.MP4</option><option value=".MOV">.MOV</option><option value=".AVI">.AVI</option>
                  </select>
                </div>
              </template>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Focus</label>
                <select disabled v-model="descentFocus" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                  <option value="auto">Auto</option><option value="manual">Manual</option>
                </select>
              </div>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">ISO</label>
                <select disabled v-model="descentISO" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                  <option value="auto">Auto</option><option value="100">100</option><option value="200">200</option><option value="400">400</option><option value="800">800</option><option value="1600">1600</option><option value="3200">3200</option>
                </select>
              </div>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">White Balance</label>
                <select disabled v-model="descentWhiteBalance" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                  <option value="auto">Auto</option><option value="underwater">Underwater</option><option value="3000k">3000K</option><option value="5500k">5500K</option><option value="6500k">6500K</option>
                </select>
              </div>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Exposure Compensation</label>
                <select disabled v-model="descentExposure" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                  <option value="-2">-2.0</option><option value="-1">-1.0</option><option value="0">0.0</option><option value="+1">+1.0</option><option value="+2">+2.0</option>
                </select>
              </div>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Sharpness</label>
                <select disabled v-model="descentSharpness" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                  <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option>
                </select>
              </div>
              <div v-if="descentCameraType === 'timelapse'">
                <label class="block mb-2 text-sm" style="color: #96EEF2">File Format</label>
                <select disabled v-model="descentFileFormat" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                  <option value="JPEG">JPEG</option><option value="TIFF">TIFF</option>
                </select>
              </div>
              <button disabled class="px-4 py-2 text-white rounded-lg cursor-not-allowed" style="background: linear-gradient(135deg, #41B9C3 0%, #96EEF2 100%)">
                Reset to Default Settings
              </button>
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
                <label class="flex items-center gap-2 cursor-not-allowed opacity-40">
                  <input type="radio" value="video-interval" v-model="bottomCameraType" disabled class="w-4 h-4" />
                  <span style="color: #96EEF2">Interval Video</span>
                </label>
                <label class="flex items-center gap-2 cursor-not-allowed opacity-40">
                  <input type="radio" value="timelapse" v-model="bottomCameraType" disabled class="w-4 h-4" />
                  <span style="color: #96EEF2">Timelapse Images</span>
                </label>
              </div>
            </div>

            <div v-if="bottomCameraType === 'timelapse'">
              <label class="block mb-2 text-sm" style="color: #96EEF2">Capture Frequency</label>
              <div class="flex gap-2">
                <input type="number" min="1" v-model.number="bottomCaptureFrequency" @input="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" />
                <select v-model="bottomCaptureFrequencyUnit" @change="hasUnsavedChanges = true" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                  <option value="seconds">Seconds</option><option value="minutes">Minutes</option><option value="hours">Hours</option>
                </select>
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

            <!-- Sleep Timer (disabled) -->
            <div class="mt-4 opacity-40">
              <label class="flex items-center gap-2 mb-2 text-sm cursor-not-allowed" style="color: #96EEF2">
                <input type="checkbox" disabled class="w-4 h-4 cursor-not-allowed" style="accent-color: #41B9C3" />
                Optional: Stop recording and go to sleep after elapsed time of:
              </label>
            </div>

            <button @click="bottomAdvancedOpen = !bottomAdvancedOpen" class="flex items-center gap-2 px-4 py-2 mt-2 rounded-lg transition-all hover:opacity-80" style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid rgba(65, 185, 195, 0.4); color: #96EEF2">
              <ChevronUp v-if="bottomAdvancedOpen" class="w-5 h-5" /><ChevronDown v-else class="w-5 h-5" />
              <span class="font-medium">Camera Settings</span>
            </button>

            <div v-if="bottomAdvancedOpen" class="p-4 rounded-lg space-y-4 opacity-50 pointer-events-none" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
              <template v-if="bottomCameraType !== 'timelapse'">
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">Resolution</label>
                  <select disabled v-model="bottomResolution" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                    <option value="4K">4K</option><option value="2.7K">2.7K</option><option value="1080p">1080p</option><option value="720p">720p</option>
                  </select>
                </div>
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">Frame Rate</label>
                  <select disabled v-model.number="bottomFrameRate" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                    <option :value="24">24 fps</option><option :value="30">30 fps</option><option :value="60">60 fps</option>
                  </select>
                </div>
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">File Format</label>
                  <select disabled v-model="bottomVideoFileFormat" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                    <option value=".MP4">.MP4</option><option value=".MOV">.MOV</option><option value=".AVI">.AVI</option>
                  </select>
                </div>
              </template>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Focus</label>
                <select disabled v-model="bottomFocus" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                  <option value="auto">Auto</option><option value="manual">Manual</option>
                </select>
              </div>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">ISO</label>
                <select disabled v-model="bottomISO" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                  <option value="auto">Auto</option><option value="100">100</option><option value="200">200</option><option value="400">400</option><option value="800">800</option><option value="1600">1600</option><option value="3200">3200</option>
                </select>
              </div>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">White Balance</label>
                <select disabled v-model="bottomWhiteBalance" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                  <option value="auto">Auto</option><option value="underwater">Underwater</option><option value="3000k">3000K</option><option value="5500k">5500K</option><option value="6500k">6500K</option>
                </select>
              </div>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Exposure Compensation</label>
                <select disabled v-model="bottomExposure" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                  <option value="-2">-2.0</option><option value="-1">-1.0</option><option value="0">0.0</option><option value="+1">+1.0</option><option value="+2">+2.0</option>
                </select>
              </div>
              <div>
                <label class="block mb-2 text-sm" style="color: #96EEF2">Sharpness</label>
                <select disabled v-model="bottomSharpness" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                  <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option>
                </select>
              </div>
              <div v-if="bottomCameraType === 'timelapse'">
                <label class="block mb-2 text-sm" style="color: #96EEF2">File Format</label>
                <select disabled v-model="bottomFileFormat" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                  <option value="JPEG">JPEG</option><option value="TIFF">TIFF</option>
                </select>
              </div>
              <button disabled class="px-4 py-2 text-white rounded-lg cursor-not-allowed" style="background: linear-gradient(135deg, #41B9C3 0%, #96EEF2 100%)">Reset to Default Settings</button>
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
                <span style="color: #96EEF2">By Elapsed Time from Dive Start</span>
              </label>
              <div v-if="releaseWeightBy === 'elapsed'" class="pl-6 space-y-3">
                <div class="flex gap-2">
                  <input type="number" v-model="releaseWeightElapsedNumber" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle" min="0" />
                  <select v-model="releaseWeightElapsedUnit" class="w-1/2 px-4 py-2 text-white rounded-lg focus:outline-none" :style="inputStyle">
                    <option value="seconds">seconds</option><option value="minutes">minutes</option><option value="hours">hours</option>
                  </select>
                </div>
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

              <button @click="ascentAdvancedOpen = !ascentAdvancedOpen" class="flex items-center gap-2 px-4 py-2 mt-2 rounded-lg transition-all hover:opacity-80" style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid rgba(65, 185, 195, 0.4); color: #96EEF2">
                <ChevronUp v-if="ascentAdvancedOpen" class="w-5 h-5" /><ChevronDown v-else class="w-5 h-5" />
                <span class="font-medium">Camera Settings</span>
              </button>

              <div v-if="ascentAdvancedOpen" class="p-4 rounded-lg space-y-4 opacity-50 pointer-events-none" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
                <template v-if="ascentCameraType !== 'timelapse'">
                  <div>
                    <label class="block mb-2 text-sm" style="color: #96EEF2">Resolution</label>
                    <select disabled v-model="ascentResolution" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                      <option value="4K">4K</option><option value="2.7K">2.7K</option><option value="1080p">1080p</option><option value="720p">720p</option>
                    </select>
                  </div>
                  <div>
                    <label class="block mb-2 text-sm" style="color: #96EEF2">Frame Rate</label>
                    <select disabled v-model.number="ascentFrameRate" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                      <option :value="24">24 fps</option><option :value="30">30 fps</option><option :value="60">60 fps</option>
                    </select>
                  </div>
                  <div>
                    <label class="block mb-2 text-sm" style="color: #96EEF2">File Format</label>
                    <select disabled v-model="ascentVideoFileFormat" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                      <option value=".MP4">.MP4</option><option value=".MOV">.MOV</option><option value=".AVI">.AVI</option>
                    </select>
                  </div>
                </template>
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">Focus</label>
                  <select disabled v-model="ascentFocus" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                    <option value="auto">Auto</option><option value="manual">Manual</option>
                  </select>
                </div>
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">ISO</label>
                  <select disabled v-model="ascentISO" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                    <option value="auto">Auto</option><option value="100">100</option><option value="200">200</option><option value="400">400</option><option value="800">800</option><option value="1600">1600</option><option value="3200">3200</option>
                  </select>
                </div>
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">White Balance</label>
                  <select disabled v-model="ascentWhiteBalance" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                    <option value="auto">Auto</option><option value="underwater">Underwater</option><option value="3000k">3000K</option><option value="5500k">5500K</option><option value="6500k">6500K</option>
                  </select>
                </div>
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">Exposure Compensation</label>
                  <select disabled v-model="ascentExposure" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                    <option value="-2">-2.0</option><option value="-1">-1.0</option><option value="0">0.0</option><option value="+1">+1.0</option><option value="+2">+2.0</option>
                  </select>
                </div>
                <div>
                  <label class="block mb-2 text-sm" style="color: #96EEF2">Sharpness</label>
                  <select disabled v-model="ascentSharpness" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                    <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option>
                  </select>
                </div>
                <div v-if="ascentCameraType === 'timelapse'">
                  <label class="block mb-2 text-sm" style="color: #96EEF2">File Format</label>
                  <select disabled v-model="ascentFileFormat" class="w-full px-4 py-2 text-white rounded-lg focus:outline-none cursor-not-allowed" :style="inputStyle">
                    <option value="JPEG">JPEG</option><option value="TIFF">TIFF</option>
                  </select>
                </div>
                <button disabled class="px-4 py-2 text-white rounded-lg cursor-not-allowed" style="background: linear-gradient(135deg, #41B9C3 0%, #96EEF2 100%)">Reset to Default Settings</button>
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
                <span style="color: #96EEF2">Estimated Dive Depth</span>
                <span class="text-white text-xl">{{ estimatedDepth || '--' }}m</span>
              </div>
            </div>
            <div class="p-4 rounded-lg" style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)">
              <div class="flex items-center justify-between mb-2">
                <span style="color: #96EEF2">Total Power Draw</span>
                <span class="text-white text-xl">{{ batteryData.totalPower.toFixed(1) }}W</span>
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
              <p class="text-white text-sm opacity-90">Setting the light brightness below 75% may result in poor image quality and reduced visibility.</p>
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
