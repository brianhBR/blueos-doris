<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  Battery,
  HardDrive,
  Satellite,
  Activity,
  AlertTriangle,
  Wifi,
  WifiOff,
  ChevronUp,
  ChevronDown,
  Droplets,
  Loader2
} from 'lucide-vue-next'
import { mdiCompassOutline } from '@mdi/js'
import {
  useBattery,
  useCameraSettings,
  useConfigurations,
  useDiveControl,
  useLocation,
  useSafeSurfaceStatus,
  useSensors,
  useStorage,
  useSystemStatus,
} from '../composables/useApi'
import AttitudeVisualization from './AttitudeVisualization.vue'
import type { SensorModule, DeploymentConfiguration } from '../composables/useApi'
import type { Screen } from '../types'
import {
  estimateDive,
  phaseConfigFromSettings,
  hoursRemainingFromSoc,
  BATTERY_CAPACITY_WH,
  BATTERY_RESERVE_PCT as PM_RESERVE_PCT,
  BATTERY_PACK_COUNT,
  BATTERY_PACK_AH,
  TYPICAL_DIVE_LOAD_W,
} from '../lib/powerModel'
import { estimateDataUsage } from '../lib/dataModel'

const props = defineProps<{
  isConnected: boolean
  releaseWeightBy: 'datetime' | 'elapsed'
}>()

const emit = defineEmits<{
  navigate: [screen: Screen, sensorName?: string]
  'update:releaseWeightBy': [value: 'datetime' | 'elapsed']
  configurationSelect: [config: string]
}>()

const { status: systemStatus, fetchStatus } = useSystemStatus()
const { battery, fetchBattery } = useBattery()
const { storage, fetchStorage } = useStorage()
const { settings: cameraSettings, fetchSettings: fetchCameraSettings } = useCameraSettings()
const { location, fetchLocation } = useLocation()
const { modules: sensorModules, loading: sensorsLoading, fetchModules } = useSensors()
const {
  status: safeSurfaceStatus,
  loading: safeSurfaceLoading,
  error: safeSurfaceError,
  fetchSafeSurfaceStatus,
} = useSafeSurfaceStatus()
const {
  status: diveStatus,
  mission: diveMission,
  startDive,
  stopDive,
  loading: diveLoading,
  fetchDiveStatus,
  fetchDiveMission,
} = useDiveControl()
const isDiving = computed(() => diveStatus.value?.active === true)

const missionPersistedLine = computed(() => {
  if (diveStatus.value?.active) return ''
  const m = diveMission.value
  if (!m || m.status === 'cancelled' || m.status === 'completed') return ''
  const n = m.configuration_name?.trim()
  if (n) return `Mission “${n}” is loaded.`
  return 'A mission is loaded on the vehicle.'
})

const batteryLevel = computed(() => battery.value?.level ?? systemStatus.value?.battery_level ?? 0)
const batteryVoltage = computed(() => {
  const v = Number(battery.value?.voltage ?? systemStatus.value?.battery_voltage ?? 0)
  return isNaN(v) ? '0.0' : v.toFixed(1)
})
const storageUsed = computed(() => storage.value?.used_percent ?? systemStatus.value?.storage_used_percent ?? 0)
const storageTotal = computed(() => storage.value?.total_gb ?? systemStatus.value?.storage_total_gb ?? 100)
const storageAvailableGb = computed(() => storage.value?.available_gb ?? (storageTotal.value - (storage.value?.used_gb ?? systemStatus.value?.storage_used_gb ?? 0)))
const storageType = computed(() => storage.value?.storage_type ?? 'SD Card')
// Live time-remaining: prefer the backend estimate (uses measured pack
// current); fall back to the shared power model's typical-load estimate.
// All pack/power constants come from ../lib/powerModel (single source).
const batteryTimeRemaining = computed(() => {
  const backend = battery.value?.time_remaining
  if (backend && backend !== 'Unknown' && backend !== 'Unavailable') return backend
  const hours = hoursRemainingFromSoc(batteryLevel.value, TYPICAL_DIVE_LOAD_W)
  const h = Math.floor(hours)
  const m = Math.round((hours - h) * 60)
  return `${h}h ${m}m`
})

const batteryEstimateAssumption = `Assumes ${BATTERY_PACK_COUNT}× ${BATTERY_PACK_AH} Ah Blue Robotics 4S packs (${BATTERY_CAPACITY_WH.toFixed(0)} Wh), ${PM_RESERVE_PCT}% reserve held back; live estimate uses measured pack current`

const gpsStatus = computed<'active' | 'searching' | 'inactive'>(() => {
  if (!location.value) return 'inactive'
  if (location.value.fix_type === 'none') return 'inactive'
  if (location.value.satellites > 0) return 'active'
  return 'searching'
})

function formatCoord(value: number, pos: string, neg: string): string {
  const dir = value >= 0 ? pos : neg
  const abs = Math.abs(value)
  const deg = Math.floor(abs)
  const min = ((abs - deg) * 60).toFixed(4)
  return `${deg}° ${min}' ${dir}`
}

const formattedLat = computed(() => {
  if (!location.value) return '—'
  return formatCoord(location.value.latitude, 'N', 'S')
})

const formattedLon = computed(() => {
  if (!location.value) return '—'
  return formatCoord(location.value.longitude, 'E', 'W')
})

const formattedFixType = computed(() => {
  if (!location.value) return '—'
  const map: Record<string, string> = { 'none': 'No Fix', '2d': '2D Fix', '3d': '3D Fix', 'dgps': 'DGPS', 'rtk_float': 'RTK Float', 'rtk_fixed': 'RTK Fixed' }
  return map[location.value.fix_type] ?? location.value.fix_type
})

const modules = computed<{ id: string; name: string; status: 'connected' | 'disconnected'; moduleStatus: string }[]>(() => {
  if (sensorModules.value.length > 0) {
    return sensorModules.value.map((m: SensorModule) => ({
      id: m.id,
      name: m.name,
      status: m.status === 'connected' ? 'connected' as const : 'disconnected' as const,
      moduleStatus: m.module_status,
    }))
  }
  return []
})

const currentUtcTimeLabel = ref('')

function refreshCurrentUtcTime() {
  const d = new Date()
  currentUtcTimeLabel.value =
    d.toLocaleTimeString(undefined, {
      timeZone: 'UTC',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }) + ' UTC'
}

let pollInterval: number | undefined
let utcClockInterval: number | undefined

onMounted(() => {
  fetchStatus()
  fetchBattery()
  fetchStorage()
  fetchLocation()
  fetchModules()
  fetchConfigurations()
  fetchDiveStatus()
  fetchDiveMission()
  fetchSafeSurfaceStatus()
  fetchCameraSettings()
  refreshCurrentUtcTime()
  pollInterval = setInterval(() => {
    fetchStatus()
    fetchBattery()
    fetchStorage()
    fetchLocation()
    fetchModules()
    fetchDiveStatus()
    fetchDiveMission()
  }, 5000) as unknown as number
  utcClockInterval = setInterval(refreshCurrentUtcTime, 1000) as unknown as number
})

onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval)
  if (utcClockInterval) clearInterval(utcClockInterval)
})

const diveName = ref('')
const username = ref('')
const selectedConfiguration = ref('')
const estimatedDepth = ref('')
const estimatedBottomTime = ref('')
const releaseWeightDate = ref('')
const releaseWeightTime = ref('')
const sortColumn = ref<'sensor' | 'status' | null>(null)
const sortDirection = ref<'asc' | 'desc'>('asc')
const leakDetected = ref(false)
const isCheckingLeaks = ref(false)

const previousUsernames = ['Captain Smith', 'Dr. Johnson', 'Prof. Lee', 'Researcher Chen']

const { configurations: savedConfigSummaries, fetchConfigurations, loadConfiguration } = useConfigurations()
const savedConfigurations = computed(() => savedConfigSummaries.value.map(c => c.name))

const loadedElapsedTimeHours = ref(0)
const loadedConfig = ref<DeploymentConfiguration | null>(null)

const depthWarningLevel = computed(() => {
  const depth = parseFloat(estimatedDepth.value)
  if (isNaN(depth) || depth <= 0) return null
  if (depth > 10900) return 'extreme'
  if (depth > 6000) return 'deep'
  return null
})

const diveFeasibility = computed(() => {
  if (!estimatedDepth.value || !selectedConfiguration.value) return null

  const depth = parseFloat(estimatedDepth.value)
  if (isNaN(depth) || depth <= 0) return null

  // Bottom time comes from the operator's release-weight setting.
  let bottomTimeHours = 0
  if (props.releaseWeightBy === 'datetime') {
    if (releaseWeightDate.value && releaseWeightTime.value) {
      const release = new Date(`${releaseWeightDate.value}T${releaseWeightTime.value}:00Z`)
      bottomTimeHours = Math.max(0, (release.getTime() - Date.now()) / 3_600_000)
    } else if (estimatedBottomTime.value) {
      bottomTimeHours = parseFloat(estimatedBottomTime.value) || 0
    }
  } else {
    bottomTimeHours = loadedElapsedTimeHours.value
  }

  // Per-phase light/camera draw from the selected configuration's real
  // settings, via the shared power model (single LED, brightness-scaled,
  // duty-cycled by camera/light mode). descent = depth/1 m/s, ascent =
  // 45 min burn + depth/1 m/s.
  const cfg = loadedConfig.value
  const estimate = estimateDive({
    depthM: depth,
    bottomTimeHours,
    descent: phaseConfigFromSettings(cfg?.descent?.light, cfg?.descent?.camera),
    bottom: phaseConfigFromSettings(cfg?.bottom?.light, cfg?.bottom?.camera),
    ascent: phaseConfigFromSettings(cfg?.ascent?.light, cfg?.ascent?.camera),
  })

  const totalDiveTimeHours = estimate.totalHours
  const batteryUsagePercent = estimate.usagePercent
  const batteryRemainingPercent = estimate.remainingPercent
  const batteryOk = batteryRemainingPercent >= 20

  // Storage estimate driven by the live camera bitrate + per-phase recording
  // mode (see lib/dataModel.ts), so it agrees with the Configuration tab's
  // Data Usage panel.  Falls back to a nominal bitrate if the camera settings
  // haven't been read yet (e.g. camera offline at the dock).
  const DEFAULT_BITRATE_KBPS = 20480 // ~20 Mbps
  const bitrateKbps = Number(cameraSettings.value?.video?.bitrate) || DEFAULT_BITRATE_KBPS
  const dataEstimate = estimateDataUsage({
    depthM: depth,
    bottomTimeHours,
    descent: phaseConfigFromSettings(cfg?.descent?.light, cfg?.descent?.camera),
    bottom: phaseConfigFromSettings(cfg?.bottom?.light, cfg?.bottom?.camera),
    ascent: phaseConfigFromSettings(cfg?.ascent?.light, cfg?.ascent?.camera),
    bitrateKbps,
    stillWidth: Number(cameraSettings.value?.video?.pic_width) || undefined,
    stillHeight: Number(cameraSettings.value?.video?.pic_height) || undefined,
  })
  const estimatedStorageNeeded = dataEstimate.totalBytes / (1024 * 1024 * 1024)
  const storageRemaining = storageAvailableGb.value - estimatedStorageNeeded
  const storageOk = storageRemaining >= 10

  let surfaceTimeUTC: Date | null = null
  let timeUntilRelease: number | null = null

  if (props.releaseWeightBy === 'datetime' && releaseWeightDate.value && releaseWeightTime.value) {
    const releaseDateTime = new Date(`${releaseWeightDate.value}T${releaseWeightTime.value}:00Z`)
    const now = new Date()
    timeUntilRelease = (releaseDateTime.getTime() - now.getTime()) / (1000 * 60 * 60)
    surfaceTimeUTC = releaseDateTime
  }

  return {
    batteryOk,
    storageOk,
    batteryRemainingPercent: Math.round(batteryRemainingPercent),
    storageRemaining: Math.round(storageRemaining),
    totalDiveTimeHours: totalDiveTimeHours.toFixed(1),
    descentTimeHours: estimate.descentHours.toFixed(1),
    bottomTimeHours: estimate.bottomHours.toFixed(1),
    ascentTimeHours: estimate.ascentHours.toFixed(1),
    batteryUsagePercent: Math.round(batteryUsagePercent),
    estimatedStorageNeeded: Math.round(estimatedStorageNeeded),
    totalPowerConsumedWh: estimate.energyWh.toFixed(1),
    batteryLifeHours: estimate.batteryLifeHours.toFixed(1),
    surfaceTimeUTC,
    timeUntilRelease: timeUntilRelease !== null ? timeUntilRelease.toFixed(1) : null
  }
})

const batteryBarColor = computed(() => {
  if (!diveFeasibility.value) return ''
  const usage = diveFeasibility.value.batteryUsagePercent
  if (usage > 80) return 'linear-gradient(90deg, #DD2C1D 0%, #FF4757 100%)'
  if (usage > 60) return 'linear-gradient(90deg, #FF9937 0%, #FFB800 100%)'
  return 'linear-gradient(90deg, #FCD869 0%, #41B9C3 100%)'
})

const sortedModules = computed(() => {
  const sorted = [...modules.value]
  if (!sortColumn.value) return sorted
  return sorted.sort((a, b) => {
    if (sortColumn.value === 'sensor') {
      return sortDirection.value === 'asc'
        ? a.name.localeCompare(b.name)
        : b.name.localeCompare(a.name)
    }
    if (sortColumn.value === 'status') {
      return sortDirection.value === 'asc'
        ? a.status.localeCompare(b.status)
        : b.status.localeCompare(a.status)
    }
    return 0
  })
})

const handleSort = (column: 'sensor' | 'status') => {
  if (sortColumn.value === column) {
    sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortColumn.value = column
    sortDirection.value = 'asc'
  }
}

const handleRecheckLeaks = () => {
  isCheckingLeaks.value = true
  setTimeout(() => {
    isCheckingLeaks.value = false
    leakDetected.value = false
  }, 2000)
}

const canStartDive = computed(() => {
  return (
    diveName.value.trim() !== '' &&
    username.value.trim() !== '' &&
    selectedConfiguration.value !== '' &&
    selectedConfiguration.value !== '__new__' &&
    estimatedDepth.value.trim() !== '' &&
    !isNaN(parseFloat(estimatedDepth.value)) &&
    parseFloat(estimatedDepth.value) > 0
    && safeSurfaceStatus.value?.ready === true
  )
})

/** Show real date/time inputs only when configuration uses datetime release and values are loaded */
const releaseWeightUtcFieldsReady = computed(
  () =>
    props.releaseWeightBy === 'datetime' &&
    Boolean(releaseWeightDate.value.trim()) &&
    Boolean(releaseWeightTime.value.trim()),
)

/** Clamp release time to 24-hour HH:MM (avoids native time picker 12-hour locale quirks). */
function normalizeReleaseWeightTime() {
  const raw = releaseWeightTime.value.trim()
  if (!raw) return
  const parts = raw.split(':').map((p) => p.trim())
  if (parts.length < 2) return
  let h = parseInt(parts[0], 10)
  let min = parseInt(parts[1], 10)
  if (Number.isNaN(h) || Number.isNaN(min)) return
  h = Math.min(23, Math.max(0, h))
  min = Math.min(59, Math.max(0, min))
  releaseWeightTime.value = `${String(h).padStart(2, '0')}:${String(min).padStart(2, '0')}`
}

function computeReleaseDateTimeFromElapsed(elapsedNumber: number, elapsedUnit: string) {
  const now = new Date()
  let offsetMs = 0
  if (elapsedUnit === 'hours') offsetMs = elapsedNumber * 3600 * 1000
  else if (elapsedUnit === 'minutes') offsetMs = elapsedNumber * 60 * 1000
  else offsetMs = elapsedNumber * 1000
  const release = new Date(now.getTime() + offsetMs)
  const yyyy = release.getUTCFullYear()
  const mm = String(release.getUTCMonth() + 1).padStart(2, '0')
  const dd = String(release.getUTCDate()).padStart(2, '0')
  releaseWeightDate.value = `${yyyy}-${mm}-${dd}`
  const hh = String(release.getUTCHours()).padStart(2, '0')
  const min = String(release.getUTCMinutes()).padStart(2, '0')
  releaseWeightTime.value = `${hh}:${min}`
}

const handleConfigurationChange = async () => {
  if (selectedConfiguration.value === '__new__') {
    selectedConfiguration.value = ''
    emit('configurationSelect', 'New Configuration')
    emit('navigate', 'dives')
    return
  }
  if (!selectedConfiguration.value) {
    releaseWeightDate.value = ''
    releaseWeightTime.value = ''
    loadedElapsedTimeHours.value = 0
    loadedConfig.value = null
    emit('configurationSelect', '')
    return
  }
  if (selectedConfiguration.value) {
    const cfg = await loadConfiguration(selectedConfiguration.value)
    if (cfg) {
      loadedConfig.value = cfg
      const rw = cfg.ascent.release_weight
      emit('update:releaseWeightBy', rw.method)
      if (rw.method === 'elapsed') {
        const num = Number(rw.elapsed.number) || 0
        loadedElapsedTimeHours.value = rw.elapsed.unit === 'hours'
          ? num
          : rw.elapsed.unit === 'minutes'
          ? num / 60
          : num / 3600
        computeReleaseDateTimeFromElapsed(num, rw.elapsed.unit)
      }
      if (rw.method === 'datetime') {
        releaseWeightDate.value = rw.release_date
        releaseWeightTime.value = rw.release_time
        normalizeReleaseWeightTime()
      }
    }
    emit('configurationSelect', selectedConfiguration.value)
  }
}

async function handleStartDive() {
  if (!canStartDive.value) return
  const currentSafety = await fetchSafeSurfaceStatus()
  if (!currentSafety?.ready) return
  const diveData: Record<string, unknown> = {
    dive_name: diveName.value.trim(),
    username: username.value.trim(),
    configuration: selectedConfiguration.value,
    estimated_depth: estimatedDepth.value.trim(),
    release_weight_date: releaseWeightDate.value,
    release_weight_time: releaseWeightTime.value,
  }
  // Capture the surface launch position so the dive record has a start
  // location (the end position is filled in from the log at dive end).
  const loc = location.value
  if (loc && loc.fix_type !== 'none' && Number.isFinite(loc.latitude) && Number.isFinite(loc.longitude)) {
    diveData.latitude = loc.latitude
    diveData.longitude = loc.longitude
  }
  await startDive(selectedConfiguration.value, diveData)
}

const formatReleaseTime = (date: Date) => {
  return date.toISOString().replace('T', ' ').substring(0, 19) + ' UTC'
}
</script>

<template>
  <div class="max-w-7xl mx-auto px-4 py-6 md:py-8">

    <!-- ======== Start New Dive ======== -->
    <div class="card-bg rounded-xl p-6 mb-8">
      <h2
        class="text-white text-xl mb-4 flex items-center gap-2"
        style="font-family: 'Montserrat', sans-serif"
      >
        <svg class="w-5 h-5" viewBox="0 0 24 24" style="color: #96EEF2">
          <path :d="mdiCompassOutline" fill="currentColor" />
        </svg>
        Start New Dive
      </h2>

      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-4">
        <div>
          <label class="block text-sm mb-2" style="color: #96EEF2">Dive Name</label>
          <input
            v-model="diveName"
            type="text"
            placeholder="Enter dive name"
            class="w-full rounded-lg px-4 py-3 text-white outline-none placeholder-gray-400"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
          />
        </div>

        <div>
          <label class="block text-sm mb-2" style="color: #96EEF2">Username</label>
          <input
            v-model="username"
            type="text"
            list="username-options"
            placeholder="Enter username"
            class="w-full rounded-lg px-4 py-3 text-white outline-none placeholder-gray-400"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
          />
          <datalist id="username-options">
            <option v-for="name in previousUsernames" :key="name" :value="name" />
          </datalist>
        </div>

        <div>
          <label class="block text-sm mb-2" style="color: #96EEF2">Load Configuration</label>
          <select
            v-model="selectedConfiguration"
            @change="handleConfigurationChange"
            class="w-full rounded-lg px-4 py-3 text-white outline-none cursor-pointer"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
          >
            <option value="" disabled style="color: gray">-- Select Configuration --</option>
            <option
              v-for="config in savedConfigurations"
              :key="config"
              :value="config"
            >
              {{ config }}
            </option>
            <option value="__new__" style="color: #FF9937">New Configuration</option>
          </select>
        </div>

        <div>
          <label class="block text-sm mb-2" style="color: #96EEF2">Estimated Depth (m)</label>
          <input
            v-model="estimatedDepth"
            type="text"
            placeholder="Enter depth"
            class="w-full rounded-lg px-4 py-3 text-white outline-none placeholder-gray-400"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
          />
          <p
            v-if="depthWarningLevel === 'extreme'"
            class="text-sm mt-1 flex items-start gap-2"
            style="color: #DD2C1D"
          >
            <AlertTriangle class="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>Woah, cowboy! You're going to need to watch the movie The Core to reach this depth.</span>
          </p>
          <p
            v-else-if="depthWarningLevel === 'deep'"
            class="text-sm mt-1 flex items-start gap-2"
            style="color: #DD2C1D"
          >
            <AlertTriangle class="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>This depth exceeds the rated depth for DORIS. Please select a depth less than 6,000 m.</span>
          </p>
        </div>

        <div>
          <label class="block text-sm mb-2" :style="{ color: releaseWeightBy === 'datetime' ? '#96EEF2' : 'rgba(150, 238, 242, 0.5)' }">Release weight date (UTC)</label>
          <input
            v-if="releaseWeightUtcFieldsReady"
            v-model="releaseWeightDate"
            type="date"
            class="w-full rounded-lg px-4 py-3 text-white outline-none"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3); color-scheme: dark"
          />
          <div
            v-else
            class="w-full rounded-lg px-4 py-3 select-none"
            style="background-color: rgba(14, 36, 70, 0.35); border: 1px solid rgba(65, 185, 195, 0.25); color: rgba(150, 238, 242, 0.55)"
          >
            —
          </div>
          <p v-if="releaseWeightBy !== 'datetime'" class="text-xs mt-1" style="color: rgba(150, 238, 242, 0.7)">
            Not shown for elapsed-based release — set mode under Configuration.
          </p>
          <p v-else-if="!releaseWeightUtcFieldsReady" class="text-xs mt-1" style="color: rgba(150, 238, 242, 0.7)">
            Load a configuration above; values appear when release is set by date and time there.
          </p>
        </div>

        <div>
          <div
            class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 mb-2"
          >
            <label
              class="text-sm"
              :style="{ color: releaseWeightBy === 'datetime' ? '#96EEF2' : 'rgba(150, 238, 242, 0.5)' }"
            >
              Release weight time (24-hour UTC)
            </label>
            <span
              class="text-xs font-mono tabular-nums whitespace-nowrap"
              style="color: #FCD869"
              title="Current time in UTC (updates every second)"
            >
              Now: {{ currentUtcTimeLabel }}
            </span>
          </div>
          <input
            v-if="releaseWeightUtcFieldsReady"
            v-model="releaseWeightTime"
            type="text"
            inputmode="numeric"
            placeholder="14:30"
            maxlength="5"
            autocomplete="off"
            spellcheck="false"
            class="w-full rounded-lg px-4 py-3 text-white outline-none font-mono tabular-nums"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
            title="24-hour UTC, format HH:MM"
            @blur="normalizeReleaseWeightTime"
          />
          <p
            v-if="releaseWeightUtcFieldsReady"
            class="text-xs mt-1"
            style="color: rgba(150, 238, 242, 0.65)"
          >
            Enter 24-hour UTC time (HH:MM), e.g. 09:05 or 21:30.
          </p>
          <div
            v-else
            class="w-full rounded-lg px-4 py-3 select-none"
            style="background-color: rgba(14, 36, 70, 0.35); border: 1px solid rgba(65, 185, 195, 0.25); color: rgba(150, 238, 242, 0.55)"
          >
            —
          </div>
          <p v-if="releaseWeightBy !== 'datetime'" class="text-xs mt-1" style="color: rgba(150, 238, 242, 0.7)">
            Not shown for elapsed-based release — set mode under Configuration.
          </p>
          <p v-else-if="!releaseWeightUtcFieldsReady" class="text-xs mt-1" style="color: rgba(150, 238, 242, 0.7)">
            Load a configuration above; values appear when release is set by date and time there.
          </p>
        </div>

        <template v-if="releaseWeightBy === 'datetime'">
          <div>
            <label class="block text-sm mb-2" style="color: #96EEF2">
              Estimated Bottom Time (hours)
            </label>
            <input
              v-model="estimatedBottomTime"
              type="number"
              placeholder="Enter hours"
              min="0"
              step="0.1"
              class="w-full rounded-lg px-4 py-3 text-white outline-none placeholder-gray-400"
              style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
            />
            <p class="text-xs mt-1" style="color: rgba(150, 238, 242, 0.7)">
              Time spent at bottom depth
            </p>
          </div>
        </template>

        <div
          v-if="safeSurfaceLoading || safeSurfaceError || safeSurfaceStatus?.ready !== true"
          class="rounded-lg p-4"
          style="background-color: rgba(221, 44, 29, 0.15); border: 1px solid rgba(221, 44, 29, 0.5)"
        >
          <div class="flex items-start gap-3">
            <Loader2 v-if="safeSurfaceLoading" class="w-5 h-5 animate-spin flex-shrink-0" style="color: #FF9937" />
            <AlertTriangle v-else class="w-5 h-5 flex-shrink-0" style="color: #FF9937" />
            <div>
              <p class="text-white font-semibold">No usable weight release path</p>
              <p v-if="safeSurfaceLoading" class="text-sm mt-1" style="color: #FCD869">Checking release paths, AGT firmware, and wiring…</p>
              <p v-else-if="safeSurfaceError" class="text-sm mt-1" style="color: #FCD869">{{ safeSurfaceError }}</p>
              <ul v-else class="text-sm mt-1 list-disc pl-5" style="color: #FCD869">
                <li v-for="message in safeSurfaceStatus?.blockers ?? []" :key="message">{{ message }}</li>
              </ul>
            </div>
          </div>
        </div>

        <div
          v-else-if="(safeSurfaceStatus?.warnings?.length ?? 0) > 0"
          class="rounded-lg p-4"
          style="background-color: rgba(255, 153, 55, 0.12); border: 1px solid rgba(255, 153, 55, 0.4)"
        >
          <div class="flex items-start gap-3">
            <AlertTriangle class="w-5 h-5 flex-shrink-0" style="color: #FF9937" />
            <div>
              <p class="text-white font-semibold">
                Release redundancy reduced — {{ safeSurfaceStatus?.navigator_release_available ? 'Navigator' : 'AGT' }} path only
              </p>
              <ul class="text-sm mt-1 list-disc pl-5" style="color: #FCD869">
                <li v-for="message in safeSurfaceStatus?.warnings ?? []" :key="message">{{ message }}</li>
              </ul>
            </div>
          </div>
        </div>

        <div class="flex items-end gap-3">
          <button
            @click="handleStartDive"
            :disabled="diveLoading || isDiving || !canStartDive"
            class="flex-1 px-6 py-3 rounded-lg text-white transition-all disabled:cursor-not-allowed"
            :style="{ backgroundColor: isDiving ? '#6B7280' : (!canStartDive ? '#6B7280' : '#FF9937'), opacity: (diveLoading || !canStartDive) ? 0.5 : 1 }"
          >
            {{ isDiving ? 'Mission loaded' : diveLoading ? 'Loading...' : 'Load Mission' }}
          </button>
          <button
            v-if="isDiving"
            @click="stopDive"
            :disabled="diveLoading"
            class="flex-1 px-6 py-3 rounded-lg text-white transition-all disabled:cursor-not-allowed"
            :style="{ backgroundColor: '#DD2C1D', opacity: diveLoading ? 0.5 : 1 }"
          >
            {{ diveLoading ? 'Cancelling...' : 'Cancel Dive' }}
          </button>
        </div>
      </div>

      <p
        v-if="isDiving"
        class="text-sm mt-3"
        style="color: #FCD869"
      >
        Mission loaded — ready for deployment.
      </p>
      <p
        v-if="missionPersistedLine"
        class="text-sm mt-1"
        style="color: #96EEF2"
      >
        {{ missionPersistedLine }}
      </p>
      <p class="text-sm" :class="isDiving || missionPersistedLine ? 'mt-2' : 'mt-0'" style="color: #96EEF2">
        Mission begins automatically after pre-arm checks pass (GPS, battery, leak, profile) and DORIS descends below the depth gate (default 3&nbsp;m).
      </p>
    </div>

    <!-- ======== Dive Feasibility & Battery Planning ======== -->
    <div v-if="diveFeasibility" class="card-bg rounded-xl p-6 mb-6">
      <h2
        class="text-white text-2xl mb-6 flex items-center gap-2"
        style="font-family: 'Montserrat', sans-serif"
      >
        <Battery class="w-5 h-5" style="color: #96EEF2" />
        Dive Feasibility &amp; Battery Planning
      </h2>

      <!-- Time Breakdown -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <p class="text-xs mb-1" style="color: #96EEF2">Total Dive Time</p>
          <p class="text-xl font-bold text-white">{{ diveFeasibility.totalDiveTimeHours }} hrs</p>
        </div>
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <p class="text-xs mb-1" style="color: #96EEF2">Descent</p>
          <p class="text-xl font-bold text-white">{{ diveFeasibility.descentTimeHours }} hrs</p>
        </div>
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <p class="text-xs mb-1" style="color: #96EEF2">Bottom Time</p>
          <p class="text-xl font-bold text-white">{{ diveFeasibility.bottomTimeHours }} hrs</p>
        </div>
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <p class="text-xs mb-1" style="color: #96EEF2">Ascent</p>
          <p class="text-xl font-bold text-white">{{ diveFeasibility.ascentTimeHours }} hrs</p>
        </div>
      </div>

      <!-- Power & Battery Metrics -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <p class="text-xs mb-1" style="color: #96EEF2">Total Power Consumed</p>
          <p class="text-xl font-bold text-white">{{ diveFeasibility.totalPowerConsumedWh }} Wh</p>
        </div>
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <p class="text-xs mb-1" style="color: #96EEF2">Battery Life</p>
          <p class="text-xl font-bold text-white">{{ diveFeasibility.batteryLifeHours }} hrs</p>
        </div>
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <p class="text-xs mb-1" style="color: #96EEF2">Battery Remaining</p>
          <p
            class="text-xl font-bold"
            :style="{ color: diveFeasibility.batteryOk ? '#FCD869' : '#DD2C1D' }"
          >
            {{ diveFeasibility.batteryRemainingPercent }}%
          </p>
        </div>
      </div>

      <!-- Battery Usage Bar -->
      <div class="mb-6">
        <div class="flex items-center justify-between mb-2">
          <span class="text-sm" style="color: #96EEF2">Battery Usage</span>
          <span
            class="text-sm font-semibold"
            :style="{ color: diveFeasibility.batteryOk ? '#FCD869' : '#DD2C1D' }"
          >
            {{ diveFeasibility.batteryUsagePercent }}%
          </span>
        </div>
        <div class="w-full rounded-full h-3" style="background-color: rgba(14, 36, 70, 0.6)">
          <div
            class="h-3 rounded-full transition-all"
            :style="{
              width: `${diveFeasibility.batteryUsagePercent}%`,
              background: batteryBarColor
            }"
          />
        </div>
      </div>

      <!-- Storage Estimation -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <p class="text-xs mb-1" style="color: #96EEF2">Estimated Storage Needed</p>
          <p class="text-xl font-bold text-white">{{ diveFeasibility.estimatedStorageNeeded }} GB</p>
        </div>
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <p class="text-xs mb-1" style="color: #96EEF2">Storage Remaining After Dive</p>
          <p
            class="text-xl font-bold"
            :style="{ color: diveFeasibility.storageOk ? '#FCD869' : '#DD2C1D' }"
          >
            {{ diveFeasibility.storageRemaining }} GB
          </p>
        </div>
      </div>

      <!-- Time Until Release (datetime mode) -->
      <div
        v-if="diveFeasibility.surfaceTimeUTC"
        class="rounded-lg p-4 mb-6"
        style="background-color: rgba(14, 36, 70, 0.5)"
      >
        <p class="text-xs mb-1" style="color: #96EEF2">Time Until Release</p>
        <p class="text-xl font-bold text-white">
          {{ diveFeasibility.timeUntilRelease }} hrs
          <span class="text-sm font-normal" style="color: #96EEF2">
            ({{ formatReleaseTime(diveFeasibility.surfaceTimeUTC) }})
          </span>
        </p>
      </div>

      <!-- Battery Warning -->
      <div
        v-if="!diveFeasibility.batteryOk"
        class="flex items-center gap-3 rounded-lg p-3 mb-3"
        style="background-color: rgba(221, 44, 29, 0.2); border: 1px solid rgba(221, 44, 29, 0.5)"
      >
        <AlertTriangle class="w-5 h-5 flex-shrink-0" style="color: #DD2C1D" />
        <p class="text-sm" style="color: #FF6B6B">
          Battery remaining is below 20%. Consider reducing dive time or equipment usage.
        </p>
      </div>

      <!-- Storage Warning -->
      <div
        v-if="!diveFeasibility.storageOk"
        class="flex items-center gap-3 rounded-lg p-3"
        style="background-color: rgba(221, 44, 29, 0.2); border: 1px solid rgba(221, 44, 29, 0.5)"
      >
        <AlertTriangle class="w-5 h-5 flex-shrink-0" style="color: #DD2C1D" />
        <p class="text-sm" style="color: #FF6B6B">
          Storage remaining is below 10 GB. Free up space or reduce recording duration.
        </p>
      </div>
    </div>

    <!-- ======== Leak Detection Alert ======== -->
    <div
      v-if="leakDetected"
      class="rounded-xl p-6 mb-6 flex flex-col md:flex-row items-start md:items-center justify-between gap-4"
      style="background-color: rgba(221, 44, 29, 0.15); border: 2px solid rgba(221, 44, 29, 0.6)"
    >
      <div class="flex items-center gap-4">
        <div class="p-3 rounded-full flex-shrink-0" style="background-color: rgba(221, 44, 29, 0.3)">
          <Droplets class="w-6 h-6" style="color: #DD2C1D" />
        </div>
        <div>
          <h3 class="text-white font-semibold text-lg">Leak Detected</h3>
          <p class="text-sm" style="color: #FF6B6B">
            A potential leak has been detected. Immediate attention required.
          </p>
        </div>
      </div>
      <button
        @click="handleRecheckLeaks"
        :disabled="isCheckingLeaks"
        class="px-6 py-2 rounded-lg text-white text-sm font-semibold transition-all hover:opacity-90 flex items-center gap-2 flex-shrink-0"
        :style="{
          background: isCheckingLeaks
            ? 'rgba(14, 36, 70, 0.5)'
            : 'linear-gradient(135deg, #DD2C1D 0%, #B52318 100%)',
          opacity: isCheckingLeaks ? 0.7 : 1
        }"
      >
        <Loader2 v-if="isCheckingLeaks" class="w-4 h-4 animate-spin" />
        {{ isCheckingLeaks ? 'Checking...' : 'Recheck' }}
      </button>
    </div>

    <!-- ======== System Status Cards ======== -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
      <!-- Battery -->
      <div class="card-bg rounded-xl p-6">
        <div class="flex items-center justify-between mb-4">
          <div class="flex items-center gap-2">
            <Battery class="w-5 h-5" style="color: #96EEF2" />
            <span class="text-white">Battery <span style="color: #96EEF2">({{ batteryVoltage }}V)</span></span>
          </div>
          <span style="color: #FCD869">{{ batteryLevel.toFixed(1) }}%</span>
        </div>
        <div class="w-full rounded-full h-2" style="background-color: rgba(14, 36, 70, 0.6)">
          <div
            class="h-2 rounded-full transition-all"
            :style="{
              width: `${batteryLevel}%`,
              background: 'linear-gradient(90deg, #FCD869 0%, #41B9C3 100%)'
            }"
          />
        </div>
        <p class="text-sm mt-2" style="color: #96EEF2" :title="batteryEstimateAssumption">
          Estimated: {{ batteryTimeRemaining }} remaining
        </p>
        <p class="text-xs mt-1 opacity-75" style="color: #96EEF2">
          2× 10 Ah 4S packs · live current · 15% reserve
        </p>
      </div>

      <!-- Storage Available -->
      <div class="card-bg rounded-xl p-6">
        <div class="flex items-center justify-between mb-4">
          <div class="flex items-center gap-2">
            <HardDrive class="w-5 h-5" style="color: #96EEF2" />
            <span class="text-white">Storage Available</span>
            <span class="text-xs px-2 py-0.5 rounded-full" style="background-color: rgba(65, 185, 195, 0.2); color: #96EEF2">{{ storageType }}</span>
          </div>
          <span style="color: #FCD869">{{ Math.round(100 - storageUsed) }}%</span>
        </div>
        <div class="w-full rounded-full h-2" style="background-color: rgba(14, 36, 70, 0.6)">
          <div
            class="h-2 rounded-full transition-all"
            :style="{
              width: `${storageUsed}%`,
              background: 'linear-gradient(90deg, #41B9C3 0%, #96EEF2 100%)'
            }"
          />
        </div>
        <p class="text-sm mt-2" style="color: #96EEF2">{{ storageAvailableGb.toFixed(1) }} GB available of {{ storageTotal.toFixed(0) }} GB</p>
      </div>

      <!-- Location -->
      <div class="card-bg rounded-xl p-6">
        <div class="flex items-center justify-between mb-4">
          <div class="flex items-center gap-2">
            <Satellite class="w-5 h-5" style="color: #96EEF2" />
            <span class="text-white">Location</span>
          </div>
          <span
            class="text-sm px-2 py-1 rounded"
            :style="gpsStatus === 'active'
              ? { backgroundColor: 'rgba(252, 216, 105, 0.2)', color: '#FCD869' }
              : gpsStatus === 'searching'
                ? { backgroundColor: 'rgba(255, 153, 55, 0.2)', color: '#FF9937' }
                : { backgroundColor: 'rgba(221, 44, 29, 0.2)', color: '#DD2C1D' }"
          >
            {{ gpsStatus === 'active' ? 'Active' : gpsStatus === 'searching' ? 'Searching' : 'Inactive' }}
          </span>
        </div>

        <template v-if="location && gpsStatus !== 'inactive'">
          <div class="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <div>
              <span style="color: rgba(150, 238, 242, 0.6)" class="text-xs uppercase tracking-wide">Lat</span>
              <p class="text-white font-mono">{{ formattedLat }}</p>
            </div>
            <div>
              <span style="color: rgba(150, 238, 242, 0.6)" class="text-xs uppercase tracking-wide">Lon</span>
              <p class="text-white font-mono">{{ formattedLon }}</p>
            </div>
            <div>
              <span style="color: rgba(150, 238, 242, 0.6)" class="text-xs uppercase tracking-wide">Satellites</span>
              <p class="text-white font-mono">{{ location.satellites }}</p>
            </div>
            <div>
              <span style="color: rgba(150, 238, 242, 0.6)" class="text-xs uppercase tracking-wide">Fix</span>
              <p class="text-white font-mono">{{ formattedFixType }}</p>
            </div>
            <div>
              <span style="color: rgba(150, 238, 242, 0.6)" class="text-xs uppercase tracking-wide">Alt</span>
              <p class="text-white font-mono">{{ location.altitude.toFixed(1) }} m</p>
            </div>
            <div>
              <span style="color: rgba(150, 238, 242, 0.6)" class="text-xs uppercase tracking-wide">Heading</span>
              <p class="text-white font-mono">{{ location.heading.toFixed(1) }}°</p>
            </div>
            <div>
              <span style="color: rgba(150, 238, 242, 0.6)" class="text-xs uppercase tracking-wide">Speed</span>
              <p class="text-white font-mono">{{ location.speed.toFixed(1) }} m/s</p>
            </div>
            <div>
              <span style="color: rgba(150, 238, 242, 0.6)" class="text-xs uppercase tracking-wide">Updated</span>
              <p class="text-white font-mono text-xs">{{ location.last_update }}</p>
            </div>
          </div>
        </template>

        <p v-else class="text-sm" style="color: #96EEF2">Use the LoRa locator device or your Iridium app to track DORIS location.</p>
      </div>
    </div>

    <!-- ======== DORIS Visualization ======== -->
    <div class="grid grid-cols-1 lg:grid-cols-4 gap-6 mb-8">
      <!-- System Overview (1 col) -->
      <div class="card-bg rounded-xl p-6">
        <h2
          class="text-white mb-4 flex items-center gap-2 text-xl"
          style="font-family: 'Montserrat', sans-serif"
        >
          <Activity class="w-5 h-5" style="color: #96EEF2" />
          System Overview
        </h2>
        <div
          class="aspect-[3/4] rounded-lg overflow-hidden"
          style="border: 1px solid rgba(65, 185, 195, 0.2)"
        >
          <AttitudeVisualization />
        </div>
      </div>

      <!-- Connected Sensors (3 cols) -->
      <div class="lg:col-span-3 card-bg rounded-xl p-6">
        <h2
          class="text-white mb-4 flex items-center gap-2 text-xl"
          style="font-family: 'Montserrat', sans-serif"
        >
          <Loader2 v-if="sensorsLoading && sortedModules.length === 0" class="w-5 h-5 animate-spin" style="color: #96EEF2" />
          <Activity v-else class="w-5 h-5" style="color: #96EEF2" />
          Connected Sensors
        </h2>

        <!-- Table Header -->
        <div
          class="grid grid-cols-2 gap-4 px-4 pb-2 mb-2 border-b"
          style="border-color: rgba(65, 185, 195, 0.3)"
        >
          <button
            class="flex items-center gap-1 text-sm text-left cursor-pointer hover:opacity-80 transition-opacity"
            style="color: #96EEF2"
            @click="handleSort('sensor')"
          >
            Sensor
            <ChevronUp
              v-if="sortColumn === 'sensor' && sortDirection === 'asc'"
              class="w-4 h-4"
            />
            <ChevronDown
              v-else-if="sortColumn === 'sensor' && sortDirection === 'desc'"
              class="w-4 h-4"
            />
            <ChevronUp v-else class="w-4 h-4 opacity-30" />
          </button>
          <button
            class="flex items-center gap-1 text-sm justify-end cursor-pointer hover:opacity-80 transition-opacity"
            style="color: #96EEF2"
            @click="handleSort('status')"
          >
            Status
            <ChevronUp
              v-if="sortColumn === 'status' && sortDirection === 'asc'"
              class="w-4 h-4"
            />
            <ChevronDown
              v-else-if="sortColumn === 'status' && sortDirection === 'desc'"
              class="w-4 h-4"
            />
            <ChevronUp v-else class="w-4 h-4 opacity-30" />
          </button>
        </div>

        <!-- Loading skeleton -->
        <div v-if="sensorsLoading && sortedModules.length === 0" class="space-y-2">
          <div
            v-for="i in 4"
            :key="i"
            class="rounded-lg p-4 grid grid-cols-2 gap-4 items-center animate-pulse"
            style="background-color: rgba(14, 36, 70, 0.5)"
          >
            <div class="flex items-center gap-3">
              <div class="w-5 h-5 rounded-full" style="background-color: rgba(150, 238, 242, 0.15)" />
              <div class="h-4 rounded" :style="{ width: `${100 + i * 20}px`, backgroundColor: 'rgba(150, 238, 242, 0.15)' }" />
            </div>
            <div class="flex justify-end">
              <div class="h-6 w-24 rounded" style="background-color: rgba(150, 238, 242, 0.1)" />
            </div>
          </div>
        </div>

        <!-- Empty state -->
        <div
          v-else-if="!sensorsLoading && sortedModules.length === 0"
          class="rounded-lg p-8 text-center"
          style="background-color: rgba(14, 36, 70, 0.3)"
        >
          <WifiOff class="w-8 h-8 mx-auto mb-3" style="color: rgba(150, 238, 242, 0.4)" />
          <p class="text-sm" style="color: rgba(150, 238, 242, 0.6)">No sensors detected</p>
        </div>

        <!-- Table Rows -->
        <div v-else class="space-y-2">
          <div
            v-for="mod in sortedModules"
            :key="mod.id"
            class="rounded-lg p-4 grid grid-cols-2 gap-4 items-center cursor-pointer transition-all hover:opacity-80"
            style="background-color: rgba(14, 36, 70, 0.5)"
            @click="emit('navigate', 'sensors', mod.name)"
          >
            <div class="flex items-center gap-3">
              <Wifi
                v-if="mod.status === 'connected'"
                class="w-5 h-5 flex-shrink-0"
                style="color: #FCD869"
              />
              <WifiOff
                v-else
                class="w-5 h-5 flex-shrink-0"
                style="color: #DD2C1D"
              />
              <p class="text-white">{{ mod.name }}</p>
            </div>
            <div class="flex justify-end">
              <div
                class="px-3 py-1 rounded text-sm"
                :style="mod.status === 'connected'
                  ? { backgroundColor: 'rgba(252, 216, 105, 0.2)', color: '#FCD869' }
                  : { backgroundColor: 'rgba(221, 44, 29, 0.2)', color: '#DD2C1D' }"
              >
                {{ mod.status === 'connected' ? 'Connected' : 'Not Connected' }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
