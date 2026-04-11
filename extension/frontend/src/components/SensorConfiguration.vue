<script setup lang="ts">
import { ref, watch, nextTick, onMounted, onUnmounted, computed } from 'vue'
import { Wifi, WifiOff, Upload, RefreshCw, Loader2, AlertCircle } from 'lucide-vue-next'
import {
  mdiCameraOutline,
  mdiVideoOutline,
  mdiLightbulbOnOutline,
  mdiThermometerLines,
  mdiWaves,
  mdiGauge,
  mdiMoleculeCo2,
  mdiBottleTonicOutline,
  mdiSatelliteUplink,
  mdiAccessPointNetwork,
  mdiRadar,
  mdiSineWave,
  mdiWaterOutline,
  mdiCogOutline,
  mdiChip,
} from '@mdi/js'
import { useSensors } from '../composables/useApi'
import type { SensorModule as ApiSensorModule } from '../composables/useApi'
import type { Screen } from '../types'

interface DisplayModule {
  id: string
  name: string
  type: string
  connected: boolean
  sampleRate?: number
  calibrationFile?: string
  moduleStatus: string
}

interface Props {
  targetSensor?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  targetSensor: null
})

const emit = defineEmits<{
  navigate: [screen: Screen]
}>()

const { modules: apiModules, loading: sensorsLoading, fetchModules } = useSensors()

const modules = ref<DisplayModule[]>([])
const selectedModule = ref<DisplayModule | null>(null)
const isDetecting = ref(false)
const moduleRefs = ref<Record<string, HTMLDivElement | null>>({})

// ── Camera snapshot state ───────────────────────────────────────────
const snapshotUrl = ref<string | null>(null)
const snapshotLoading = ref(false)
const snapshotError = ref(false)
let snapshotInterval: number | undefined

const hasConnectedCamera = computed(() =>
  modules.value.some(m => m.type === 'camera' && m.connected)
)

async function refreshSnapshot() {
  if (!hasConnectedCamera.value) return
  snapshotLoading.value = !snapshotUrl.value
  snapshotError.value = false
  try {
    const resp = await fetch(`/api/v1/camera/snapshot?_t=${Date.now()}`)
    if (!resp.ok) throw new Error(resp.statusText)
    const blob = await resp.blob()
    if (snapshotUrl.value) URL.revokeObjectURL(snapshotUrl.value)
    snapshotUrl.value = URL.createObjectURL(blob)
  } catch {
    snapshotError.value = true
  } finally {
    snapshotLoading.value = false
  }
}

function startSnapshotPolling() {
  if (snapshotInterval) { clearInterval(snapshotInterval); snapshotInterval = undefined }
  if (hasConnectedCamera.value) {
    snapshotLoading.value = true
    refreshSnapshot()
    snapshotInterval = setInterval(refreshSnapshot, 10000) as unknown as number
  } else {
    if (snapshotUrl.value) { URL.revokeObjectURL(snapshotUrl.value); snapshotUrl.value = null }
    snapshotError.value = false
  }
}

// ── Tracker GPS data ────────────────────────────────────────────────
interface TrackerGPS {
  fix_type: number
  fix_type_name: string
  lat: number
  lon: number
  alt_m: number
  satellites: number
  hdop: number | null
  speed_mps: number
  course_deg: number
  last_update: string | null
}

const trackerGps = ref<TrackerGPS | null>(null)
let trackerInterval: number | undefined

async function refreshTrackerGps() {
  try {
    const resp = await fetch('/api/v1/tracker/gps')
    if (resp.ok) {
      trackerGps.value = await resp.json()
    }
  } catch { /* best effort */ }
}

const hasTracker = computed(() =>
  modules.value.some(m => m.type === 'tracker' && m.connected)
)

function startTrackerPolling() {
  if (trackerInterval) { clearInterval(trackerInterval); trackerInterval = undefined }
  if (hasTracker.value) {
    refreshTrackerGps()
    trackerInterval = setInterval(refreshTrackerGps, 5000) as unknown as number
  } else {
    trackerGps.value = null
  }
}

watch(hasTracker, startTrackerPolling)

// ── Iridium test ─────────────────────────────────────────────────────
type IridiumState = 'idle' | 'sending' | 'running' | 'passed' | 'failed'
const iridiumState = ref<IridiumState>('idle')
const iridiumMessage = ref('')
let iridiumPollInterval: number | undefined
let iridiumBaselineCounter = -1

async function triggerIridiumTest() {
  if (iridiumState.value === 'sending' || iridiumState.value === 'running') return
  iridiumState.value = 'sending'
  iridiumMessage.value = ''

  const baseline = await fetch('/api/v1/tracker/iridium-status')
  if (baseline.ok) {
    const data = await baseline.json()
    iridiumBaselineCounter = data.counter ?? 0
  }

  try {
    const resp = await fetch('/api/v1/tracker/iridium-test', { method: 'POST' })
    if (!resp.ok) throw new Error('Request failed')
    const result = await resp.json()
    if (!result.accepted) {
      iridiumState.value = 'failed'
      iridiumMessage.value = result.error || 'Command rejected'
      return
    }
    iridiumState.value = 'running'
    iridiumMessage.value = 'Test starting — this may take 2–10 minutes…'
    startIridiumPolling()
  } catch {
    iridiumState.value = 'failed'
    iridiumMessage.value = 'Failed to send command'
  }
}

function startIridiumPolling() {
  stopIridiumPolling()
  iridiumPollInterval = setInterval(pollIridiumStatus, 3000) as unknown as number
}

function stopIridiumPolling() {
  if (iridiumPollInterval) { clearInterval(iridiumPollInterval); iridiumPollInterval = undefined }
}

async function pollIridiumStatus() {
  try {
    const resp = await fetch('/api/v1/tracker/iridium-status')
    if (!resp.ok) return
    const data = await resp.json()
    if (!data.text || data.counter <= iridiumBaselineCounter) return

    const text: string = data.text
    if (text.includes('PASSED')) {
      iridiumState.value = 'passed'
      iridiumMessage.value = text
      stopIridiumPolling()
    } else if (text.includes('FAILED')) {
      iridiumState.value = 'failed'
      iridiumMessage.value = text
      stopIridiumPolling()
    } else if (text.includes('IRIDIUM')) {
      iridiumMessage.value = text
    }
  } catch { /* best effort */ }
}

function resetIridiumTest() {
  stopIridiumPolling()
  iridiumState.value = 'idle'
  iridiumMessage.value = ''
}

// ── Light test button ───────────────────────────────────────────────
const lightTestActive = ref(false)
const lightTestError = ref('')
let lightKeepAlive: number | undefined

async function setLightBrightness(brightness: number): Promise<boolean> {
  try {
    const resp = await fetch('/api/v1/lights/brightness', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ brightness }),
    })
    const data = await resp.json()
    if (!data.success) {
      const msg = data.error || `Light test failed (HTTP ${resp.status})`
      console.warn('[DORIS] Light test error:', msg)
      lightTestError.value = msg
      return false
    }
    lightTestError.value = ''
    return true
  } catch (e) {
    const msg = e instanceof Error ? e.message : 'Network error'
    console.warn('[DORIS] Light test fetch failed:', msg)
    lightTestError.value = msg
    return false
  }
}

async function lightTestOn() {
  if (lightTestActive.value) return
  lightTestActive.value = true
  lightTestError.value = ''
  const ok = await setLightBrightness(10)
  if (!ok || !lightTestActive.value) {
    lightTestActive.value = false
    return
  }
  lightKeepAlive = setInterval(() => setLightBrightness(10), 1500) as unknown as number
}

function lightTestOff() {
  const wasActive = lightTestActive.value
  lightTestActive.value = false
  if (lightKeepAlive) { clearInterval(lightKeepAlive); lightKeepAlive = undefined }
  if (wasActive) {
    setLightBrightness(0)
    setTimeout(() => setLightBrightness(0), 300)
  }
}

// ── Module list sync ────────────────────────────────────────────────
watch(apiModules, (newModules) => {
  if (newModules.length > 0) {
    const hadCamera = hasConnectedCamera.value
    modules.value = newModules.map((m: ApiSensorModule) => ({
      id: m.id,
      name: m.name,
      type: m.type,
      connected: m.status === 'connected',
      sampleRate: m.sample_rate ?? undefined,
      calibrationFile: m.firmware_version ?? undefined,
      moduleStatus: m.module_status,
    }))
    if (!hadCamera && hasConnectedCamera.value) {
      startSnapshotPolling()
    }
  }
}, { immediate: true })

watch(hasConnectedCamera, startSnapshotPolling)

let pollInterval: number | undefined

onMounted(() => {
  fetchModules()
  pollInterval = setInterval(fetchModules, 5000) as unknown as number
})

onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval)
  if (snapshotInterval) clearInterval(snapshotInterval)
  if (trackerInterval) clearInterval(trackerInterval)
  if (lightKeepAlive) clearInterval(lightKeepAlive)
  stopIridiumPolling()
  if (snapshotUrl.value) URL.revokeObjectURL(snapshotUrl.value)
})

const sensorMapping: Record<string, string> = {
  'Camera': 'Camera',
  'Light': 'Light',
  'Conductivity': 'Conductivity',
  'Temperature': 'Temperature',
  'Depth': 'Depth',
  'CO2': 'Carbon Dioxide (CO₂)',
  'O2': 'Oxygen (O₂)',
  'Iridium': 'Iridium',
  'LoRa': 'LoRa',
}

watch(() => props.targetSensor, (sensor) => {
  if (!sensor) return
  const mappedName = sensorMapping[sensor] || sensor
  const target = modules.value.find(m => m.name === mappedName)
  if (target) {
    selectedModule.value = target
    nextTick(() => {
      setTimeout(() => {
        moduleRefs.value[target.id]?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }, 100)
    })
  }
}, { immediate: true })

const detectSensors = async () => {
  isDetecting.value = true
  await fetchModules()
  isDetecting.value = false
}

const toggleConnection = (id: string) => {
  modules.value = modules.value.map(m =>
    m.id === id ? { ...m, connected: !m.connected } : m
  )
}

const setModuleRef = (id: string, el: HTMLDivElement | null) => {
  moduleRefs.value[id] = el
}

const getModuleIcon = (mod: DisplayModule): string => {
  const name = mod.name.toLowerCase()
  const type = mod.type.toLowerCase()

  if (type === 'camera' || name.includes('camera')) return mdiCameraOutline
  if (type === 'video' || name.includes('video') || name.includes('stream')) return mdiVideoOutline
  if (type === 'light' || name.includes('light')) return mdiLightbulbOnOutline
  if (name.includes('thermometer') || name.includes('temperature') || name.includes('temp')) return mdiThermometerLines
  if (name.includes('barometer')) return mdiGauge
  if (name.includes('depth') || name.includes('pressure')) return mdiWaves
  if (name.includes('conductivity') || name.includes('ctd')) return mdiSineWave
  if (name.includes('co2') || name.includes('co₂') || name.includes('carbon')) return mdiMoleculeCo2
  if (name.includes('o2') || name.includes('o₂') || name.includes('oxygen')) return mdiBottleTonicOutline
  if (name.includes('iridium')) return mdiSatelliteUplink
  if (name.includes('lora')) return mdiAccessPointNetwork
  if (name.includes('ping') || name.includes('sonar')) return mdiRadar
  if (name.includes('water') || name.includes('leak')) return mdiWaterOutline
  if (type === 'tracker') return mdiSatelliteUplink
  if (type === 'communication') return mdiAccessPointNetwork
  if (type === 'sensor') return mdiChip

  return mdiGauge
}

const getStatusColor = (moduleStatus: string) => {
  if (moduleStatus.includes('Warning')) return '#FF9937'
  if (moduleStatus.includes('Disconnected')) return '#DD2C1D'
  return '#FCD869'
}
</script>

<template>
  <div class="max-w-6xl mx-auto px-4 py-6 md:py-8">
    <div
      class="backdrop-blur-sm rounded-xl p-6 border"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
        <h1 class="text-white text-2xl flex items-center gap-2">
          <Loader2 v-if="sensorsLoading" class="w-6 h-6 animate-spin" style="color: #96EEF2" />
          <svg v-else class="w-6 h-6" viewBox="0 0 24 24" style="color: #96EEF2">
            <path :d="mdiGauge" fill="currentColor" />
          </svg>
          Sensor Status
        </h1>
        <button
          @click="detectSensors"
          :disabled="isDetecting"
          class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 flex items-center gap-2"
          :style="{
            background: 'linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)',
            opacity: isDetecting ? 0.7 : 1
          }"
        >
          <RefreshCw :class="['w-4 h-4', isDetecting && 'animate-spin']" />
          Refresh Sensors
        </button>
      </div>

      <!-- Loading skeleton -->
      <div v-if="sensorsLoading && modules.length === 0" class="space-y-4">
        <div
          v-for="i in 4"
          :key="i"
          class="rounded-lg p-4 animate-pulse"
          style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)"
        >
          <div class="flex items-center justify-between mb-3">
            <div class="flex items-center gap-3">
              <div class="w-6 h-6 rounded" style="background-color: rgba(150, 238, 242, 0.15)" />
              <div class="h-4 rounded" :style="{ width: `${90 + i * 25}px`, backgroundColor: 'rgba(150, 238, 242, 0.15)' }" />
            </div>
            <div class="w-9 h-9 rounded-lg" style="background-color: rgba(150, 238, 242, 0.1)" />
          </div>
          <div class="space-y-2">
            <div class="flex justify-between">
              <div class="h-3 w-20 rounded" style="background-color: rgba(150, 238, 242, 0.1)" />
              <div class="h-3 w-24 rounded" style="background-color: rgba(150, 238, 242, 0.1)" />
            </div>
            <div class="flex justify-between">
              <div class="h-3 w-24 rounded" style="background-color: rgba(150, 238, 242, 0.1)" />
              <div class="h-3 w-20 rounded" style="background-color: rgba(150, 238, 242, 0.1)" />
            </div>
          </div>
        </div>
      </div>

      <!-- Empty state -->
      <div
        v-else-if="!sensorsLoading && modules.length === 0"
        class="rounded-lg p-10 text-center"
        style="background-color: rgba(14, 36, 70, 0.3); border: 1px solid rgba(65, 185, 195, 0.15)"
      >
        <WifiOff class="w-10 h-10 mx-auto mb-3" style="color: rgba(150, 238, 242, 0.4)" />
        <p class="text-white mb-1">No sensors detected</p>
        <p class="text-sm" style="color: rgba(150, 238, 242, 0.6)">Click "Refresh Sensors" to scan for connected devices.</p>
      </div>

      <!-- Module List -->
      <div v-if="modules.length > 0" class="space-y-4">
        <div
          v-for="mod in modules"
          :key="mod.id"
          :ref="(el) => setModuleRef(mod.id, el as HTMLDivElement | null)"
        >
          <div
            class="p-4 transition-all cursor-pointer"
            :style="{
              backgroundColor: selectedModule?.id === mod.id ? 'rgba(65, 185, 195, 0.2)' : 'rgba(14, 36, 70, 0.5)',
              border: selectedModule?.id === mod.id ? '1px solid #41B9C3' : '1px solid rgba(65, 185, 195, 0.2)',
              borderRadius: selectedModule?.id === mod.id ? '0.5rem 0.5rem 0 0' : '0.5rem',
              borderBottom: selectedModule?.id === mod.id ? 'none' : '1px solid rgba(65, 185, 195, 0.2)'
            }"
            @click="selectedModule = selectedModule?.id === mod.id ? null : mod"
          >
            <div class="flex items-center justify-between mb-3">
              <div class="flex items-center gap-3">
                <svg class="w-6 h-6" viewBox="0 0 24 24" style="color: #96EEF2">
                  <path :d="getModuleIcon(mod)" fill="currentColor" />
                </svg>
                <div>
                  <h3 class="text-white">{{ mod.name }}</h3>
                </div>
              </div>
              <button
                @click.stop="toggleConnection(mod.id)"
                class="p-2 rounded-lg transition-all"
                :style="{
                  backgroundColor: mod.connected ? 'rgba(252, 216, 105, 0.2)' : 'rgba(14, 36, 70, 0.5)',
                  color: mod.connected ? '#FCD869' : '#96EEF2'
                }"
              >
                <Wifi v-if="mod.connected" class="w-5 h-5" />
                <WifiOff v-else class="w-5 h-5" />
              </button>
            </div>

            <div class="space-y-2">
              <div class="flex items-center justify-between text-sm">
                <span :style="{ color: getStatusColor(mod.moduleStatus) }">
                  {{ mod.moduleStatus }}
                </span>
              </div>
              <div class="flex items-center justify-between text-sm">
                <span style="color: #96EEF2">Connection</span>
                <span :style="{ color: mod.connected ? '#FCD869' : '#DD2C1D' }">
                  {{ mod.connected ? 'Connected' : 'Disconnected' }}
                </span>
              </div>
            </div>

            <!-- Inline camera preview -->
            <div v-if="mod.type === 'camera' && mod.connected" class="mt-3 rounded-lg overflow-hidden" style="border: 1px solid rgba(65, 185, 195, 0.2)">
              <div
                v-if="snapshotLoading && !snapshotUrl"
                class="flex items-center justify-center py-10"
                style="color: #96EEF2; background-color: rgba(0,0,0,0.3)"
              >
                <Loader2 class="w-5 h-5 animate-spin mr-2" />
                <span class="text-xs">Connecting to camera...</span>
              </div>

              <div
                v-else-if="snapshotError && !snapshotUrl"
                class="flex items-center justify-center gap-2 py-6"
                style="background-color: rgba(0,0,0,0.3)"
              >
                <AlertCircle class="w-4 h-4" style="color: rgba(150, 238, 242, 0.4)" />
                <span class="text-xs" style="color: rgba(150, 238, 242, 0.5)">Preview unavailable</span>
                <button
                  @click.stop="refreshSnapshot"
                  class="text-xs px-2 py-0.5 rounded hover:opacity-80"
                  style="background-color: rgba(65, 185, 195, 0.2); color: #96EEF2; border: 1px solid rgba(65, 185, 195, 0.3)"
                >Retry</button>
              </div>

              <div v-else-if="snapshotUrl" style="background-color: #000">
                <img
                  :src="snapshotUrl"
                  alt="Camera preview"
                  class="w-full object-contain"
                  style="max-height: 300px"
                />
                <div class="flex items-center justify-end gap-1 px-2 py-1" style="background-color: rgba(14, 36, 70, 0.8)">
                  <span class="text-xs" style="color: rgba(150, 238, 242, 0.5)">Snapshot updates every 10s</span>
                </div>
              </div>

            </div>

            <!-- Inline light test button -->
            <div v-if="mod.type === 'light' && mod.connected" class="mt-3">
              <button
                class="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm transition-all select-none"
                :style="{
                  backgroundColor: lightTestError
                    ? 'rgba(239, 68, 68, 0.2)'
                    : lightTestActive
                      ? 'rgba(252, 216, 105, 0.3)'
                      : 'rgba(14, 36, 70, 0.5)',
                  border: lightTestError
                    ? '1px solid rgba(239, 68, 68, 0.6)'
                    : lightTestActive
                      ? '1px solid #FCD869'
                      : '1px solid rgba(65, 185, 195, 0.2)',
                  color: lightTestError ? '#F87171' : lightTestActive ? '#FCD869' : '#96EEF2',
                }"
                @mousedown.prevent="lightTestOn"
                @mouseup.prevent="lightTestOff"
                @mouseleave="lightTestOff"
                @touchstart.prevent="lightTestOn"
                @touchend.prevent="lightTestOff"
                @touchcancel="lightTestOff"
              >
                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
                  <path :d="mdiLightbulbOnOutline" />
                </svg>
                {{ lightTestActive ? 'Light ON (10%)' : 'Hold to Test Light' }}
              </button>
              <p v-if="lightTestError" class="mt-1.5 text-xs" style="color: #F87171">
                {{ lightTestError }}
              </p>
            </div>

            <!-- Inline tracker GPS data -->
            <div v-if="mod.type === 'tracker' && mod.connected && trackerGps" class="mt-3 rounded-lg overflow-hidden" style="border: 1px solid rgba(65, 185, 195, 0.2)">
              <div class="grid grid-cols-2 gap-px" style="background-color: rgba(65, 185, 195, 0.1)">
                <div class="px-3 py-2" style="background-color: rgba(14, 36, 70, 0.7)">
                  <div class="text-xs" style="color: rgba(150, 238, 242, 0.5)">Fix</div>
                  <div class="text-sm font-medium" :style="{ color: trackerGps.fix_type >= 3 ? '#96EEF2' : trackerGps.fix_type >= 2 ? '#FCD869' : 'rgba(150,238,242,0.4)' }">
                    {{ trackerGps.fix_type_name }}
                  </div>
                </div>
                <div class="px-3 py-2" style="background-color: rgba(14, 36, 70, 0.7)">
                  <div class="text-xs" style="color: rgba(150, 238, 242, 0.5)">Satellites</div>
                  <div class="text-sm font-medium" style="color: #96EEF2">{{ trackerGps.satellites }}</div>
                </div>
                <div class="px-3 py-2" style="background-color: rgba(14, 36, 70, 0.7)">
                  <div class="text-xs" style="color: rgba(150, 238, 242, 0.5)">Latitude</div>
                  <div class="text-sm font-medium" style="color: #96EEF2">{{ trackerGps.fix_type >= 2 ? trackerGps.lat.toFixed(6) + '°' : '—' }}</div>
                </div>
                <div class="px-3 py-2" style="background-color: rgba(14, 36, 70, 0.7)">
                  <div class="text-xs" style="color: rgba(150, 238, 242, 0.5)">Longitude</div>
                  <div class="text-sm font-medium" style="color: #96EEF2">{{ trackerGps.fix_type >= 2 ? trackerGps.lon.toFixed(6) + '°' : '—' }}</div>
                </div>
                <div class="px-3 py-2" style="background-color: rgba(14, 36, 70, 0.7)">
                  <div class="text-xs" style="color: rgba(150, 238, 242, 0.5)">Altitude</div>
                  <div class="text-sm font-medium" style="color: #96EEF2">{{ trackerGps.fix_type >= 2 ? trackerGps.alt_m.toFixed(1) + ' m' : '—' }}</div>
                </div>
                <div class="px-3 py-2" style="background-color: rgba(14, 36, 70, 0.7)">
                  <div class="text-xs" style="color: rgba(150, 238, 242, 0.5)">HDOP</div>
                  <div class="text-sm font-medium" style="color: #96EEF2">{{ trackerGps.hdop != null ? trackerGps.hdop.toFixed(1) : '—' }}</div>
                </div>
              </div>
              <div class="flex items-center justify-end px-2 py-1" style="background-color: rgba(14, 36, 70, 0.8)">
                <span class="text-xs" style="color: rgba(150, 238, 242, 0.4)">Updates every 5s</span>
              </div>
            </div>

            <!-- Iridium test button -->
            <div v-if="mod.type === 'tracker' && mod.connected" class="mt-3">
              <button
                :disabled="iridiumState === 'sending' || iridiumState === 'running'"
                class="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm transition-all"
                :style="{
                  backgroundColor: iridiumState === 'passed' ? 'rgba(34, 197, 94, 0.2)'
                    : iridiumState === 'failed' ? 'rgba(239, 68, 68, 0.2)'
                    : iridiumState === 'running' || iridiumState === 'sending' ? 'rgba(252, 216, 105, 0.15)'
                    : 'rgba(14, 36, 70, 0.5)',
                  border: iridiumState === 'passed' ? '1px solid rgba(34, 197, 94, 0.5)'
                    : iridiumState === 'failed' ? '1px solid rgba(239, 68, 68, 0.5)'
                    : iridiumState === 'running' || iridiumState === 'sending' ? '1px solid rgba(252, 216, 105, 0.4)'
                    : '1px solid rgba(65, 185, 195, 0.2)',
                  color: iridiumState === 'passed' ? '#22c55e'
                    : iridiumState === 'failed' ? '#ef4444'
                    : iridiumState === 'running' || iridiumState === 'sending' ? '#FCD869'
                    : '#96EEF2',
                  opacity: iridiumState === 'sending' || iridiumState === 'running' ? '0.85' : '1',
                  cursor: iridiumState === 'sending' || iridiumState === 'running' ? 'not-allowed' : 'pointer',
                }"
                @click="iridiumState === 'passed' || iridiumState === 'failed' ? resetIridiumTest() : triggerIridiumTest()"
              >
                <Loader2 v-if="iridiumState === 'sending' || iridiumState === 'running'" class="w-4 h-4 animate-spin" />
                <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
                  <path :d="mdiSatelliteUplink" />
                </svg>
                <span v-if="iridiumState === 'idle'">Iridium Test</span>
                <span v-else-if="iridiumState === 'sending'">Sending…</span>
                <span v-else-if="iridiumState === 'running'">Testing…</span>
                <span v-else-if="iridiumState === 'passed' || iridiumState === 'failed'">{{ iridiumMessage || 'Done' }} — Tap to reset</span>
              </button>
              <p v-if="iridiumState === 'running' && iridiumMessage" class="mt-1 text-xs text-center" style="color: rgba(252, 216, 105, 0.7)">
                {{ iridiumMessage }}
              </p>
            </div>
          </div>

          <!-- Expanded Configuration -->
          <div
            v-if="selectedModule?.id === mod.id"
            class="p-6 animate-slide-in"
            style="background-color: rgba(65, 185, 195, 0.15); border: 1px solid #41B9C3; border-top: 1px solid rgba(65, 185, 195, 0.3); border-radius: 0 0 0.5rem 0.5rem"
          >
            <h2 class="text-white mb-4 flex items-center gap-2">
              <svg class="w-5 h-5" viewBox="0 0 24 24" style="color: #96EEF2">
                <path :d="mdiCogOutline" fill="currentColor" />
              </svg>
              {{ selectedModule.name }} Configuration
            </h2>

            <!-- Sensor type config -->
            <div v-if="selectedModule.type === 'sensor'" class="space-y-4">
              <div>
                <label class="block text-sm mb-2" style="color: #96EEF2">Calibration File</label>
                <div class="flex gap-2">
                  <select
                    class="flex-1 px-4 py-2 text-white rounded-lg focus:outline-none"
                    style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
                  >
                    <option>Default Calibration</option>
                    <option>{{ selectedModule.calibrationFile }}</option>
                    <option>ctd_cal_2023.cal</option>
                  </select>
                  <button
                    class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 flex items-center gap-2"
                    style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
                  >
                    <Upload class="w-4 h-4" />
                    Upload
                  </button>
                </div>
              </div>
            </div>

            <!-- Camera type config -->
            <div v-if="selectedModule.type === 'camera'" class="space-y-4">
              <p class="text-sm" style="color: rgba(150, 238, 242, 0.6)">
                Camera-specific settings are available in the Configuration section.
              </p>
              <button
                @click="emit('navigate', 'dives')"
                class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90"
                style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
              >
                Go to Configuration
              </button>
            </div>

            <!-- Light type config -->
            <div v-if="selectedModule.type === 'light'" class="space-y-4">
              <h3 class="mb-3" style="color: #96EEF2">Light Configuration</h3>
              <p style="color: #96EEF2">Lighting-specific settings are available in the Configuration section.</p>
              <button
                @click="emit('navigate', 'dives')"
                class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90"
                style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
              >
                Go to Configuration
              </button>
            </div>

            <!-- Communication type config -->
            <div v-if="selectedModule.type === 'communication'" class="space-y-4">
              <p style="color: #96EEF2">Communication-specific settings are available in the Configuration section.</p>
              <button
                @click="emit('navigate', 'dives')"
                class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90"
                style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
              >
                Go to Configuration
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
