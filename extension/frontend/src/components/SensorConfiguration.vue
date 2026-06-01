<script setup lang="ts">
import { ref, watch, nextTick, onMounted, onUnmounted, computed } from 'vue'
import { Wifi, WifiOff, Upload, RefreshCw, Loader2, AlertCircle, Video, Square } from 'lucide-vue-next'
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
import { useSensors, useConductivity } from '../composables/useApi'
import type {
  SensorModule as ApiSensorModule,
  ConductivityCalibration,
} from '../composables/useApi'
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

const { modules: apiModules, loading: sensorsLoading, fetchModules, calibrateBarometer } = useSensors()

const modules = ref<DisplayModule[]>([])
const selectedModule = ref<DisplayModule | null>(null)
const isDetecting = ref(false)
const moduleRefs = ref<Record<string, HTMLDivElement | null>>({})

// ── Camera snapshot state ───────────────────────────────────────────
// The preview hits /api/v1/camera/snapshot, which fetches a single JPEG
// straight from the IP camera's built-in snapshot CGI (~200 ms, no MCM,
// no ffmpeg, no RTSP).  That means the preview works whenever the
// camera itself is reachable on the network — the BlueOS Camera Manager
// stream does NOT need to be running.  The only time it is unavailable
// is while the onboard recorder pipeline is active, in which case the
// backend returns HTTP 409 and we render a "preview disabled while
// recording" tile instead of polling.
const SNAPSHOT_POLL_MS = 10000

const snapshotUrl = ref<string | null>(null)
const snapshotLoading = ref(false)
const snapshotError = ref(false)
/** Truth source for whether the camera is actually reachable.  Driven
 *  by snapshot outcomes (not by MCM's stream.running, which lies for
 *  the direct-CGI path).  null until the first probe completes; a 409
 *  from the recorder does NOT change this -- it means the camera is
 *  busy, not unreachable. */
const cameraReachable = ref<boolean | null>(null)
let snapshotInterval: number | undefined

/** Any camera tile from Camera Manager (running or not) — the direct-CGI
 *  snapshot path does not require stream.running. */
const hasCameraModule = computed(() => modules.value.some(m => m.type === 'camera'))

function clearSnapshotImage() {
  if (snapshotUrl.value) {
    URL.revokeObjectURL(snapshotUrl.value)
    snapshotUrl.value = null
  }
}

async function refreshSnapshot() {
  if (!hasCameraModule.value) return
  // While the recorder owns the camera's single RTSP/snapshot slot,
  // skip polling entirely instead of hammering the backend with 409s.
  // The recorder being busy does NOT mean the camera is unreachable,
  // so leave cameraReachable alone.
  if (ipcamRecording.value) {
    clearSnapshotImage()
    snapshotLoading.value = false
    snapshotError.value = false
    return
  }
  snapshotLoading.value = !snapshotUrl.value
  snapshotError.value = false
  try {
    const resp = await fetch(`/api/v1/camera/snapshot?_t=${Date.now()}`)
    if (resp.status === 409) {
      // Backend says recorder is active; status pill is driven by
      // ipcamRecording, so don't flip cameraReachable here either.
      clearSnapshotImage()
      return
    }
    if (!resp.ok) throw new Error(resp.statusText)
    const blob = await resp.blob()
    if (snapshotUrl.value) URL.revokeObjectURL(snapshotUrl.value)
    snapshotUrl.value = URL.createObjectURL(blob)
    cameraReachable.value = true
  } catch {
    snapshotError.value = true
    cameraReachable.value = false
  } finally {
    snapshotLoading.value = false
  }
}

function startSnapshotPolling() {
  if (snapshotInterval) { clearInterval(snapshotInterval); snapshotInterval = undefined }
  if (hasCameraModule.value) {
    snapshotLoading.value = !snapshotUrl.value
    void refreshSnapshot()
    snapshotInterval = setInterval(refreshSnapshot, SNAPSHOT_POLL_MS) as unknown as number
  } else {
    clearSnapshotImage()
    snapshotError.value = false
    cameraReachable.value = null
  }
}

/** Header-row state for a camera tile.  Replaces the misleading
 *  mod.connected / mod.moduleStatus values (which derive from MCM
 *  stream.running) with values that reflect actual camera reachability
 *  via the snapshot endpoint plus the recorder's busy-state. */
type CameraTileTone = 'good' | 'warn' | 'bad' | 'neutral'
interface CameraTileStatus {
  statusText: string
  statusColor: string
  pillText: string
  pillColor: string
  icon: 'wifi' | 'wifi-off' | 'video' | 'loader'
  iconTone: CameraTileTone
}

const cameraTileStatus = computed<CameraTileStatus>(() => {
  if (ipcamRecording.value) {
    return {
      statusText: 'Recording',
      statusColor: '#86efac',
      pillText: 'Busy (recorder)',
      pillColor: '#86efac',
      icon: 'video',
      iconTone: 'good',
    }
  }
  if (cameraReachable.value === true) {
    return {
      statusText: 'Ready: Preview live',
      statusColor: '#FCD869',
      pillText: 'Connected',
      pillColor: '#FCD869',
      icon: 'wifi',
      iconTone: 'good',
    }
  }
  if (cameraReachable.value === false) {
    return {
      statusText: 'No response from camera',
      statusColor: '#DD2C1D',
      pillText: 'Unreachable',
      pillColor: '#DD2C1D',
      icon: 'wifi-off',
      iconTone: 'bad',
    }
  }
  return {
    statusText: 'Connecting...',
    statusColor: 'rgba(150, 238, 242, 0.7)',
    pillText: 'Connecting...',
    pillColor: 'rgba(150, 238, 242, 0.7)',
    icon: 'loader',
    iconTone: 'neutral',
  }
})

// ── IP camera extension recorder (ffmpeg RTSP → TS; no Lua) ───────────
const IPCAM_RECORD_API = '/api/v1/ipcam/record'
const ipcamRecording = ref(false)
const ipcamRecordBusy = ref(false)
const ipcamRecordError = ref('')
const IPCAM_SPLIT_SEC = 300
let ipcamStatusInterval: number | undefined

async function refreshIpcamRecordStatus() {
  if (!hasCameraModule.value) return
  try {
    const resp = await fetch(`${IPCAM_RECORD_API}/status`)
    if (!resp.ok) return
    const data = await resp.json()
    ipcamRecording.value = Boolean(data.recording)
  } catch {
    /* best effort */
  }
}

function startIpcamStatusPolling() {
  if (ipcamStatusInterval) {
    clearInterval(ipcamStatusInterval)
    ipcamStatusInterval = undefined
  }
  if (hasCameraModule.value) {
    void refreshIpcamRecordStatus()
    ipcamStatusInterval = setInterval(refreshIpcamRecordStatus, 4000) as unknown as number
  } else {
    ipcamRecording.value = false
  }
}

function startSnapshotSidecar() {
  startSnapshotPolling()
}

function startIpcamSidecar() {
  startIpcamStatusPolling()
}

async function startIpcamRecording() {
  if (ipcamRecordBusy.value) return
  ipcamRecordBusy.value = true
  ipcamRecordError.value = ''
  try {
    const resp = await fetch(
      `${IPCAM_RECORD_API}/start?split_duration=${IPCAM_SPLIT_SEC}`,
      { method: 'POST' },
    )
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok || data.success === false) {
      ipcamRecordError.value =
        (data && (data.message as string)) || `Start failed (HTTP ${resp.status})`
      return
    }
    ipcamRecording.value = true
  } catch (e) {
    ipcamRecordError.value = e instanceof Error ? e.message : 'Start failed'
  } finally {
    ipcamRecordBusy.value = false
    void refreshIpcamRecordStatus()
  }
}

async function stopIpcamRecording() {
  if (ipcamRecordBusy.value) return
  ipcamRecordBusy.value = true
  ipcamRecordError.value = ''
  try {
    const resp = await fetch(`${IPCAM_RECORD_API}/stop`, { method: 'POST' })
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok || data.success === false) {
      ipcamRecordError.value =
        (data && (data.message as string)) || `Stop failed (HTTP ${resp.status})`
      return
    }
    ipcamRecording.value = false
  } catch (e) {
    ipcamRecordError.value = e instanceof Error ? e.message : 'Stop failed'
  } finally {
    ipcamRecordBusy.value = false
    void refreshIpcamRecordStatus()
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
//
// The AGT runs the SBD test for 2-10 minutes and emits a handful of
// `IRIDIUM: …` STATUSTEXT messages along the way (`Test starting`,
// `Test PASSED`, `Test FAILED`, occasional progress).  In between it
// keeps spamming `GPS: …` warnings, and mavlink2rest only caches the
// latest STATUSTEXT.  We therefore poll the backend with `since_id`
// and walk every new buffered AGT message — that way the PASSED/FAILED
// can never be overwritten before we see it.
type IridiumState = 'idle' | 'sending' | 'running' | 'passed' | 'failed'
interface IridiumMessage {
  id: number
  text: string
  severity: number  // MAV_SEVERITY: 0=EMERGENCY .. 3=ERROR, 4=WARNING, 6=INFO
  timestamp: string
}
const iridiumState = ref<IridiumState>('idle')
const iridiumMessage = ref('')
const iridiumElapsedMs = ref(0)
// Transcript of messages observed during the current test (IRIDIUM:* and recent GPS/RTC context).
// Capped at 50 to avoid unbounded growth on a long test.
const iridiumTranscript = ref<IridiumMessage[]>([])
const iridiumDetailsOpen = ref(false)
const iridiumDetailsEl = ref<HTMLElement | null>(null)
// Push order can interleave (synthetic "triggered" lands first, then async
// seed back-fills boot/GPS rows that have OLDER timestamps, then live polls
// append). Sort by timestamp so the panel always reads chronologically.
const sortedIridiumTranscript = computed(() => {
  return [...iridiumTranscript.value].sort((a, b) => a.timestamp.localeCompare(b.timestamp))
})
// AGT firmware fast-fails an SBD attempt with no GPS fix anyway
// ("IRIDIUM: Test skipped (no GPS fix)"), so block the test up front
// and tell the user why.  The AGT also uses 2D fix as the threshold.
const iridiumGpsBlocked = computed(() => {
  const fix = trackerGps.value?.fix_type ?? 0
  return fix < 2
})
const iridiumButtonDisabled = computed(() => {
  return iridiumState.value === 'sending'
    || iridiumState.value === 'running'
    || (iridiumState.value === 'idle' && iridiumGpsBlocked.value)
})
let iridiumPollInterval: number | undefined
let iridiumElapsedTimer: number | undefined
let iridiumLastSeenId = 0
let iridiumStartedAt = 0
let iridiumDeadline = 0
const IRIDIUM_TEST_MAX_MS = 12 * 60 * 1000  // hard cap; AGT should resolve in <10 min

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

function pushTranscript(m: IridiumMessage) {
  // Only keep IRIDIUM:* and short context (GPS/RTC) so the panel stays useful.
  if (!/^(IRIDIUM|GPS|RTC):/i.test(m.text)) return
  iridiumTranscript.value.push(m)
  if (iridiumTranscript.value.length > 50) {
    iridiumTranscript.value.splice(0, iridiumTranscript.value.length - 50)
  }
  // Defer scroll until Vue rerenders the (sorted) list.
  nextTick(() => {
    const el = iridiumDetailsEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

async function triggerIridiumTest() {
  if (iridiumState.value === 'sending' || iridiumState.value === 'running') return
  iridiumState.value = 'sending'
  iridiumMessage.value = ''
  iridiumTranscript.value = []
  iridiumElapsedMs.value = 0
  iridiumDetailsOpen.value = false

  try {
    const resp = await fetch('/api/v1/tracker/iridium-test', { method: 'POST' })
    if (!resp.ok) throw new Error('Request failed')
    const result = await resp.json()
    if (!result.accepted) {
      iridiumState.value = 'failed'
      iridiumMessage.value = result.error || 'Command rejected'
      return
    }
    iridiumLastSeenId = typeof result.latest_id === 'number' ? result.latest_id : 0
    iridiumStartedAt = Date.now()
    iridiumDeadline = iridiumStartedAt + IRIDIUM_TEST_MAX_MS
    iridiumState.value = 'running'
    iridiumMessage.value = 'Test starting — this may take 2–10 minutes…'
    // Seed the transcript with recent context (boot, GPS lock, RTC sync) so
    // the user can open the details panel immediately. The AGT typically
    // goes silent during the SBD attempt itself, so without seeding the
    // panel would stay empty for ~10 minutes.
    seedTranscriptFromBuffer().catch(() => { /* best effort */ })
    // Always show at least a "Test triggered" row so the details panel
    // never appears empty.
    iridiumTranscript.value.push({
      id: 0,
      text: `IRIDIUM: Test triggered from UI`,
      severity: 6,
      timestamp: new Date().toISOString(),
    })
    startIridiumPolling()
    startIridiumElapsedTimer()
  } catch {
    iridiumState.value = 'failed'
    iridiumMessage.value = 'Failed to send command'
  }
}

async function seedTranscriptFromBuffer() {
  // Fetch the last ~10 messages currently in the backend buffer and prepend
  // any IRIDIUM/GPS/RTC ones to the transcript (without advancing
  // iridiumLastSeenId — the polling loop still owns that).
  const resp = await fetch('/api/v1/tracker/iridium-status?since_id=0')
  if (!resp.ok) return
  const data = await resp.json()
  const messages: IridiumMessage[] = Array.isArray(data.messages) ? data.messages : []
  const recent = messages.slice(-10)
  for (const m of recent) pushTranscript(m)
}

function startIridiumPolling() {
  stopIridiumPolling()
  iridiumPollInterval = setInterval(pollIridiumStatus, 3000) as unknown as number
}

function stopIridiumPolling() {
  if (iridiumPollInterval) { clearInterval(iridiumPollInterval); iridiumPollInterval = undefined }
}

function startIridiumElapsedTimer() {
  stopIridiumElapsedTimer()
  iridiumElapsedTimer = setInterval(() => {
    if (iridiumStartedAt) iridiumElapsedMs.value = Date.now() - iridiumStartedAt
  }, 1000) as unknown as number
}

function stopIridiumElapsedTimer() {
  if (iridiumElapsedTimer) { clearInterval(iridiumElapsedTimer); iridiumElapsedTimer = undefined }
}

function finishIridiumTest(state: 'passed' | 'failed', summary: string) {
  if (iridiumStartedAt) iridiumElapsedMs.value = Date.now() - iridiumStartedAt
  iridiumState.value = state
  iridiumMessage.value = summary
  stopIridiumPolling()
  stopIridiumElapsedTimer()
}

async function pollIridiumStatus() {
  if (iridiumDeadline && Date.now() > iridiumDeadline) {
    finishIridiumTest('failed', 'Timed out — no AGT result after 12 min')
    return
  }
  try {
    const resp = await fetch(`/api/v1/tracker/iridium-status?since_id=${iridiumLastSeenId}`)
    if (!resp.ok) return
    const data = await resp.json()
    const messages: IridiumMessage[] = Array.isArray(data.messages) ? data.messages : []
    if (typeof data.latest_id === 'number' && data.latest_id > iridiumLastSeenId) {
      iridiumLastSeenId = data.latest_id
    }
    for (const m of messages) {
      pushTranscript(m)
      const text = m.text || ''
      if (!text.startsWith('IRIDIUM')) continue
      // Treat anything emitted at WARNING (4) or worse as a terminal failure;
      // only PASSED at INFO (6) is success. Everything else (e.g. "Test
      // starting") is interim progress.
      if (text.includes('PASSED')) {
        finishIridiumTest('passed', text)
        return
      }
      if (text.includes('FAILED') || (typeof m.severity === 'number' && m.severity <= 4 && !text.includes('starting'))) {
        finishIridiumTest('failed', text)
        return
      }
      iridiumMessage.value = text
    }
  } catch { /* best effort */ }
}

function resetIridiumTest() {
  stopIridiumPolling()
  stopIridiumElapsedTimer()
  iridiumState.value = 'idle'
  iridiumMessage.value = ''
  iridiumStartedAt = 0
  iridiumDeadline = 0
  iridiumElapsedMs.value = 0
  iridiumTranscript.value = []
  iridiumDetailsOpen.value = false
}

function severityLabel(sev: number): string {
  // MAVLink MAV_SEVERITY enum
  switch (sev) {
    case 0: return 'EMERG'
    case 1: return 'ALERT'
    case 2: return 'CRIT'
    case 3: return 'ERROR'
    case 4: return 'WARN'
    case 5: return 'NOTICE'
    case 6: return 'INFO'
    case 7: return 'DEBUG'
    default: return `sev${sev}`
  }
}

function severityColor(sev: number): string {
  if (sev <= 3) return '#ef4444'      // ERROR or worse — red
  if (sev === 4) return '#FCD869'     // WARNING — amber
  return 'rgba(150, 238, 242, 0.7)'   // info/debug — cyan tint
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

// ── Barometer surface calibration ────────────────────────────────────
type BaroCalState = 'idle' | 'calibrating' | 'done' | 'error'
const baroCalState = ref<BaroCalState>('idle')
const baroCalMessage = ref('')

async function triggerBaroCalibration() {
  if (baroCalState.value === 'calibrating') return
  baroCalState.value = 'calibrating'
  baroCalMessage.value = ''
  try {
    const result = await calibrateBarometer()
    if (result.success) {
      baroCalState.value = 'done'
      baroCalMessage.value = result.message || 'Calibration done'
    } else {
      baroCalState.value = 'error'
      baroCalMessage.value = result.error || 'Calibration failed'
    }
  } catch (e) {
    baroCalState.value = 'error'
    baroCalMessage.value = e instanceof Error ? e.message : 'Calibration failed'
  }
  setTimeout(() => {
    if (baroCalState.value === 'done' || baroCalState.value === 'error') {
      baroCalState.value = 'idle'
      baroCalMessage.value = ''
    }
  }, 5000)
}

// ── Conductivity probe (AD5933 on i2c6) ──────────────────────────────
const { status: conductivityStatus, fetchConductivity, readOnce: readConductivityOnce, readCalibration } = useConductivity()
let conductivityInterval: number | undefined

const hasConductivity = computed(() =>
  modules.value.some(m => m.id === 'conductivity')
)

/** Live tile values, derived from the polled status. */
const conductivityDisplay = computed(() => {
  const reading = conductivityStatus.value?.reading
  if (!reading) return null
  const hasCal = reading.conductivity_uscm != null
  return {
    value: hasCal ? reading.conductivity_uscm! : reading.raw_conductance_ms,
    unit: hasCal ? 'µS/cm' : 'mS',
    raw: reading.raw_conductance_ms,
    magnitude: reading.magnitude,
    timestamp: reading.timestamp,
  }
})

function startConductivityPolling() {
  if (conductivityInterval) { clearInterval(conductivityInterval); conductivityInterval = undefined }
  if (hasConductivity.value) {
    void fetchConductivity()
    conductivityInterval = setInterval(fetchConductivity, 2000) as unknown as number
  }
}

watch(hasConductivity, startConductivityPolling)

// One-shot manual reading (bench/debug button).
const condReadBusy = ref(false)
const condReadError = ref('')
async function takeConductivityReading() {
  if (condReadBusy.value) return
  condReadBusy.value = true
  condReadError.value = ''
  try {
    await readConductivityOnce()
    await fetchConductivity()
  } catch (e) {
    condReadError.value = e instanceof Error ? e.message : 'Read failed'
  } finally {
    condReadBusy.value = false
  }
}

// EEPROM calibration read (bench-only helper).
type CondCalState = 'idle' | 'reading' | 'done' | 'error'
const condCalState = ref<CondCalState>('idle')
const condCalError = ref('')
const condCal = ref<ConductivityCalibration | null>(null)
async function readConductivityCalibration() {
  if (condCalState.value === 'reading') return
  condCalState.value = 'reading'
  condCalError.value = ''
  try {
    condCal.value = await readCalibration()
    condCalState.value = 'done'
  } catch (e) {
    condCalState.value = 'error'
    condCalError.value = e instanceof Error ? e.message : 'Calibration read failed'
  }
}

// ── Module list sync ────────────────────────────────────────────────
watch(apiModules, (newModules) => {
  if (newModules.length > 0) {
    const hadCamera = hasCameraModule.value
    modules.value = newModules.map((m: ApiSensorModule) => ({
      id: m.id,
      name: m.name,
      type: m.type,
      connected: m.status === 'connected',
      sampleRate: m.sample_rate ?? undefined,
      calibrationFile: m.firmware_version ?? undefined,
      moduleStatus: m.module_status,
    }))
    if (!hadCamera && hasCameraModule.value) {
      startSnapshotSidecar()
    }
  }
}, { immediate: true })

watch(hasCameraModule, () => {
  startSnapshotSidecar()
  startIpcamSidecar()
})

// When recording starts, the next poll already short-circuits (see
// refreshSnapshot); clear the stale image so the UI flips to the
// "recorder is using the camera" tile.  When recording stops, kick a
// fresh poll immediately instead of waiting for the next 10 s tick.
watch(ipcamRecording, (recording) => {
  if (recording) {
    clearSnapshotImage()
  } else if (hasCameraModule.value) {
    void refreshSnapshot()
  }
})

let pollInterval: number | undefined

onMounted(() => {
  fetchModules()
  pollInterval = setInterval(fetchModules, 5000) as unknown as number
})

onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval)
  if (snapshotInterval) clearInterval(snapshotInterval)
  if (ipcamStatusInterval) clearInterval(ipcamStatusInterval)
  if (trackerInterval) clearInterval(trackerInterval)
  if (conductivityInterval) clearInterval(conductivityInterval)
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
              <!--
                Camera tiles: read-only indicator driven by snapshot
                reachability (truth) plus recorder busy-state.  The
                old Wifi toggle for cameras was misleading because it
                flipped a cosmetic local boolean fed by MCM
                stream.running, which is no longer the truth source.

                Other tiles keep the existing local toggle.
              -->
              <div
                v-if="mod.type === 'camera'"
                class="p-2 rounded-lg flex items-center justify-center"
                :title="cameraTileStatus.pillText"
                :style="{
                  backgroundColor:
                    cameraTileStatus.iconTone === 'good' ? 'rgba(252, 216, 105, 0.2)' :
                    cameraTileStatus.iconTone === 'bad'  ? 'rgba(221, 44, 29, 0.18)' :
                    'rgba(14, 36, 70, 0.5)',
                  color:
                    cameraTileStatus.iconTone === 'good' ? '#FCD869' :
                    cameraTileStatus.iconTone === 'bad'  ? '#DD2C1D' :
                    '#96EEF2'
                }"
              >
                <Wifi    v-if="cameraTileStatus.icon === 'wifi'"     class="w-5 h-5" />
                <WifiOff v-else-if="cameraTileStatus.icon === 'wifi-off'" class="w-5 h-5" />
                <Video   v-else-if="cameraTileStatus.icon === 'video'"    class="w-5 h-5" />
                <Loader2 v-else-if="cameraTileStatus.icon === 'loader'"   class="w-5 h-5 animate-spin" />
              </div>
              <button
                v-else
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
              <!-- Camera: status + connection rows reflect actual reachability -->
              <template v-if="mod.type === 'camera'">
                <div class="flex items-center justify-between text-sm">
                  <span :style="{ color: cameraTileStatus.statusColor }">
                    {{ cameraTileStatus.statusText }}
                  </span>
                </div>
                <div class="flex items-center justify-between text-sm">
                  <span style="color: #96EEF2">Connection</span>
                  <span :style="{ color: cameraTileStatus.pillColor }">
                    {{ cameraTileStatus.pillText }}
                  </span>
                </div>
              </template>
              <template v-else>
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
              </template>
            </div>

            <!--
              Camera preview tile.  Driven by /api/v1/camera/snapshot,
              which fetches a JPEG straight from the camera's snapshot
              CGI.  It works whether or not the BlueOS Camera Manager
              stream is running — the only "off" state is while the
              onboard recorder owns the camera's single RTSP slot.
            -->
            <div v-if="mod.type === 'camera'" class="mt-3 space-y-3">
              <div
                class="rounded-lg overflow-hidden"
                style="border: 1px solid rgba(65, 185, 195, 0.2)"
              >
                <div
                  v-if="ipcamRecording"
                  class="flex items-center justify-center gap-2 py-6 text-center px-4"
                  style="background-color: rgba(0,0,0,0.35)"
                >
                  <Video class="w-4 h-4" style="color: #86efac" />
                  <span class="text-xs" style="color: rgba(150, 238, 242, 0.7)">
                    Preview paused — recorder is using the camera. Stop recording to resume preview.
                  </span>
                </div>

                <div
                  v-else-if="snapshotLoading && !snapshotUrl"
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
                  <div class="flex items-center justify-between gap-2 px-2 py-1" style="background-color: rgba(14, 36, 70, 0.8)">
                    <span class="text-xs" style="color: rgba(150, 238, 242, 0.5)">Snapshot updates every 10s</span>
                    <button
                      @click.stop="refreshSnapshot"
                      :disabled="snapshotLoading"
                      class="text-xs px-2 py-0.5 rounded hover:opacity-80 flex items-center gap-1 disabled:opacity-50"
                      style="background-color: rgba(65, 185, 195, 0.15); color: #96EEF2; border: 1px solid rgba(65, 185, 195, 0.3)"
                    >
                      <Loader2 v-if="snapshotLoading" class="w-3 h-3 animate-spin" />
                      <RefreshCw v-else class="w-3 h-3" />
                      Refresh
                    </button>
                  </div>
                </div>
              </div>

              <div class="rounded-lg p-3 space-y-2" style="background-color: rgba(14, 36, 70, 0.6); border: 1px solid rgba(65, 185, 195, 0.25)">
                <div class="flex items-center justify-between gap-2">
                  <span class="text-xs font-medium" style="color: #96EEF2">IP camera file recording</span>
                  <span
                    class="text-xs px-2 py-0.5 rounded"
                    :style="{
                      backgroundColor: ipcamRecording ? 'rgba(34, 197, 94, 0.2)' : 'rgba(14, 36, 70, 0.8)',
                      color: ipcamRecording ? '#86efac' : 'rgba(150, 238, 242, 0.6)',
                      border: ipcamRecording ? '1px solid rgba(34, 197, 94, 0.4)' : '1px solid rgba(65, 185, 195, 0.2)',
                    }"
                  >
                    {{ ipcamRecording ? 'Recording' : 'Idle' }}
                  </span>
                </div>
                <p class="text-xs leading-relaxed" style="color: rgba(150, 238, 242, 0.65)">
                  Directly drives the DORIS extension recorder (RTSP → segmented .ts on disk). Does not use the dive Lua script.
                </p>
                <div class="flex flex-wrap gap-2">
                  <button
                    type="button"
                    :disabled="ipcamRecordBusy || ipcamRecording"
                    class="flex-1 min-w-[8rem] flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                    style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%); color: #fff; border: 1px solid rgba(65, 185, 195, 0.4)"
                    @click.stop="startIpcamRecording()"
                  >
                    <Loader2 v-if="ipcamRecordBusy && !ipcamRecording" class="w-4 h-4 animate-spin" />
                    <Video v-else class="w-4 h-4" />
                    Start recording
                  </button>
                  <button
                    type="button"
                    :disabled="ipcamRecordBusy || !ipcamRecording"
                    class="flex-1 min-w-[8rem] flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                    style="background-color: rgba(221, 44, 29, 0.15); color: #fca5a5; border: 1px solid rgba(221, 44, 29, 0.45)"
                    @click.stop="stopIpcamRecording()"
                  >
                    <Loader2 v-if="ipcamRecordBusy && ipcamRecording" class="w-4 h-4 animate-spin" />
                    <Square v-else class="w-4 h-4" />
                    Stop recording
                  </button>
                </div>
                <p v-if="ipcamRecordError" class="text-xs" style="color: #f87171">{{ ipcamRecordError }}</p>
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

            <!-- Inline conductivity live readout -->
            <div v-if="mod.id === 'conductivity'" class="mt-3 rounded-lg overflow-hidden" style="border: 1px solid rgba(65, 185, 195, 0.2)">
              <div v-if="conductivityDisplay" class="grid grid-cols-3 gap-px" style="background-color: rgba(65, 185, 195, 0.1)">
                <div class="px-3 py-2" style="background-color: rgba(14, 36, 70, 0.7)">
                  <div class="text-xs" style="color: rgba(150, 238, 242, 0.5)">Conductivity</div>
                  <div class="text-sm font-medium" style="color: #96EEF2">
                    {{ conductivityDisplay.value.toFixed(3) }} {{ conductivityDisplay.unit }}
                  </div>
                </div>
                <div class="px-3 py-2" style="background-color: rgba(14, 36, 70, 0.7)">
                  <div class="text-xs" style="color: rgba(150, 238, 242, 0.5)">Raw conductance</div>
                  <div class="text-sm font-medium" style="color: #96EEF2">{{ conductivityDisplay.raw.toFixed(3) }} mS</div>
                </div>
                <div class="px-3 py-2" style="background-color: rgba(14, 36, 70, 0.7)">
                  <div class="text-xs" style="color: rgba(150, 238, 242, 0.5)">|Z| magnitude</div>
                  <div class="text-sm font-medium" style="color: #96EEF2">{{ conductivityDisplay.magnitude.toFixed(0) }}</div>
                </div>
              </div>
              <div v-else class="px-3 py-3 text-center text-xs" style="background-color: rgba(14, 36, 70, 0.7); color: rgba(150, 238, 242, 0.5)">
                {{ conductivityStatus?.last_error ? `Error: ${conductivityStatus.last_error}` : 'Waiting for first reading…' }}
              </div>
              <div class="flex items-center justify-between px-2 py-1" style="background-color: rgba(14, 36, 70, 0.8)">
                <span class="text-xs" style="color: rgba(150, 238, 242, 0.4)">Published as NAMED_VALUE_FLOAT • updates every 2s</span>
              </div>
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
                :disabled="iridiumButtonDisabled"
                :title="iridiumState === 'idle' && iridiumGpsBlocked ? 'Waiting for AGT GPS fix — the test would be skipped' : ''"
                class="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm transition-all"
                :style="{
                  backgroundColor: iridiumState === 'passed' ? 'rgba(34, 197, 94, 0.2)'
                    : iridiumState === 'failed' ? 'rgba(239, 68, 68, 0.2)'
                    : iridiumState === 'running' || iridiumState === 'sending' ? 'rgba(252, 216, 105, 0.15)'
                    : iridiumGpsBlocked ? 'rgba(14, 36, 70, 0.3)'
                    : 'rgba(14, 36, 70, 0.5)',
                  border: iridiumState === 'passed' ? '1px solid rgba(34, 197, 94, 0.5)'
                    : iridiumState === 'failed' ? '1px solid rgba(239, 68, 68, 0.5)'
                    : iridiumState === 'running' || iridiumState === 'sending' ? '1px solid rgba(252, 216, 105, 0.4)'
                    : iridiumGpsBlocked ? '1px solid rgba(150, 238, 242, 0.1)'
                    : '1px solid rgba(65, 185, 195, 0.2)',
                  color: iridiumState === 'passed' ? '#22c55e'
                    : iridiumState === 'failed' ? '#ef4444'
                    : iridiumState === 'running' || iridiumState === 'sending' ? '#FCD869'
                    : iridiumGpsBlocked ? 'rgba(150, 238, 242, 0.4)'
                    : '#96EEF2',
                  opacity: iridiumButtonDisabled ? '0.6' : '1',
                  cursor: iridiumButtonDisabled ? 'not-allowed' : 'pointer',
                }"
                @click="iridiumState === 'passed' || iridiumState === 'failed' ? resetIridiumTest() : triggerIridiumTest()"
              >
                <Loader2 v-if="iridiumState === 'sending' || iridiumState === 'running'" class="w-4 h-4 animate-spin" />
                <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
                  <path :d="mdiSatelliteUplink" />
                </svg>
                <span v-if="iridiumState === 'idle' && iridiumGpsBlocked">Iridium Test — waiting for GPS fix</span>
                <span v-else-if="iridiumState === 'idle'">Iridium Test</span>
                <span v-else-if="iridiumState === 'sending'">Sending…</span>
                <span v-else-if="iridiumState === 'running'">Testing… {{ formatElapsed(iridiumElapsedMs) }}</span>
                <span v-else-if="iridiumState === 'passed' || iridiumState === 'failed'">
                  {{ iridiumMessage || 'Done' }}
                  <span v-if="iridiumElapsedMs > 0" style="opacity: 0.7"> ({{ formatElapsed(iridiumElapsedMs) }})</span>
                  — Tap to reset
                </span>
              </button>
              <p v-if="iridiumState === 'running' && iridiumMessage" class="mt-1 text-xs text-center" style="color: rgba(252, 216, 105, 0.7)">
                {{ iridiumMessage }}
              </p>
              <p v-else-if="iridiumState === 'idle' && iridiumGpsBlocked" class="mt-1 text-xs text-center" style="color: rgba(150, 238, 242, 0.5)">
                AGT has no GPS fix yet ({{ trackerGps?.fix_type_name || 'No GPS' }}, {{ trackerGps?.satellites ?? 0 }} sats). The AGT firmware would skip the test.
              </p>
              <!-- Expandable transcript: useful for diagnosing why a test failed -->
              <div v-if="iridiumTranscript.length > 0 && (iridiumState === 'failed' || iridiumState === 'passed' || iridiumState === 'running')" class="mt-2">
                <button
                  type="button"
                  class="text-xs underline transition-opacity hover:opacity-100"
                  style="color: rgba(150, 238, 242, 0.7); opacity: 0.85"
                  @click="iridiumDetailsOpen = !iridiumDetailsOpen"
                >
                  {{ iridiumDetailsOpen ? 'Hide details' : `Show details (${iridiumTranscript.length} message${iridiumTranscript.length === 1 ? '' : 's'})` }}
                </button>
                <div
                  v-if="iridiumDetailsOpen"
                  ref="iridiumDetailsEl"
                  class="mt-1 rounded p-2 text-xs font-mono overflow-y-auto"
                  style="max-height: 12rem; background-color: rgba(0, 0, 0, 0.25); border: 1px solid rgba(65, 185, 195, 0.2)"
                >
                  <div v-for="(m, idx) in sortedIridiumTranscript" :key="`${m.id}-${idx}`" class="py-0.5">
                    <span style="color: rgba(150, 238, 242, 0.5)">{{ m.timestamp.substring(11, 19) }}</span>
                    <span class="ml-2 inline-block w-12 text-center rounded text-[10px]" :style="{ color: severityColor(m.severity), border: `1px solid ${severityColor(m.severity)}40` }">{{ severityLabel(m.severity) }}</span>
                    <span class="ml-2" :style="{ color: severityColor(m.severity) }">{{ m.text }}</span>
                  </div>
                </div>
              </div>
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

            <!-- Barometer: surface calibration -->
            <div v-if="selectedModule.id === 'barometer'" class="space-y-4">
              <p class="text-sm" style="color: rgba(150, 238, 242, 0.6)">
                Perform a surface calibration to set the current pressure reading as the reference. Ensure the vehicle is at the surface before calibrating.
              </p>
              <button
                :disabled="baroCalState === 'calibrating'"
                class="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm transition-all"
                :style="{
                  backgroundColor: baroCalState === 'done' ? 'rgba(34, 197, 94, 0.2)'
                    : baroCalState === 'error' ? 'rgba(239, 68, 68, 0.2)'
                    : baroCalState === 'calibrating' ? 'rgba(252, 216, 105, 0.15)'
                    : 'rgba(14, 36, 70, 0.5)',
                  border: baroCalState === 'done' ? '1px solid rgba(34, 197, 94, 0.5)'
                    : baroCalState === 'error' ? '1px solid rgba(239, 68, 68, 0.5)'
                    : baroCalState === 'calibrating' ? '1px solid rgba(252, 216, 105, 0.4)'
                    : '1px solid rgba(65, 185, 195, 0.2)',
                  color: baroCalState === 'done' ? '#22c55e'
                    : baroCalState === 'error' ? '#ef4444'
                    : baroCalState === 'calibrating' ? '#FCD869'
                    : '#96EEF2',
                  opacity: baroCalState === 'calibrating' ? '0.85' : '1',
                  cursor: baroCalState === 'calibrating' ? 'not-allowed' : 'pointer',
                }"
                @click="triggerBaroCalibration"
              >
                <Loader2 v-if="baroCalState === 'calibrating'" class="w-4 h-4 animate-spin" />
                <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor">
                  <path :d="mdiGauge" />
                </svg>
                <span v-if="baroCalState === 'idle'">Surface Calibrate</span>
                <span v-else-if="baroCalState === 'calibrating'">Calibrating…</span>
                <span v-else-if="baroCalState === 'done'">Calibration done</span>
                <span v-else-if="baroCalState === 'error'">Calibration failed</span>
              </button>
              <p v-if="baroCalMessage" class="text-xs text-center" :style="{ color: baroCalState === 'error' ? '#f87171' : 'rgba(150, 238, 242, 0.6)' }">
                {{ baroCalMessage }}
              </p>
            </div>

            <!-- Thermometer: no calibration -->
            <div v-else-if="selectedModule.id === 'thermometer'" class="space-y-4">
              <p class="text-sm" style="color: rgba(150, 238, 242, 0.6)">
                No calibration options available for this sensor.
              </p>
            </div>

            <!-- Conductivity probe: live reading + EEPROM calibration helper -->
            <div v-else-if="selectedModule.id === 'conductivity'" class="space-y-4">
              <p class="text-sm" style="color: rgba(150, 238, 242, 0.6)">
                The AD5933 probe is read over i2c6 and published as a NAMED_VALUE_FLOAT.
                Take a one-shot reading to verify wiring, or read the probe's stored
                calibration coefficients and copy the suggested values into the
                DORIS_CONDUCTIVITY_* extension settings.
              </p>

              <button
                :disabled="condReadBusy"
                class="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm transition-all"
                :style="{
                  backgroundColor: 'rgba(14, 36, 70, 0.5)',
                  border: '1px solid rgba(65, 185, 195, 0.2)',
                  color: '#96EEF2',
                  opacity: condReadBusy ? '0.7' : '1',
                  cursor: condReadBusy ? 'not-allowed' : 'pointer',
                }"
                @click="takeConductivityReading"
              >
                <Loader2 v-if="condReadBusy" class="w-4 h-4 animate-spin" />
                <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><path :d="mdiSineWave" /></svg>
                Take Reading
              </button>
              <p v-if="condReadError" class="text-xs text-center" style="color: #f87171">{{ condReadError }}</p>

              <button
                :disabled="condCalState === 'reading'"
                class="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm transition-all"
                :style="{
                  backgroundColor: condCalState === 'done' ? 'rgba(34, 197, 94, 0.2)'
                    : condCalState === 'error' ? 'rgba(239, 68, 68, 0.2)'
                    : condCalState === 'reading' ? 'rgba(252, 216, 105, 0.15)'
                    : 'rgba(14, 36, 70, 0.5)',
                  border: condCalState === 'done' ? '1px solid rgba(34, 197, 94, 0.5)'
                    : condCalState === 'error' ? '1px solid rgba(239, 68, 68, 0.5)'
                    : condCalState === 'reading' ? '1px solid rgba(252, 216, 105, 0.4)'
                    : '1px solid rgba(65, 185, 195, 0.2)',
                  color: condCalState === 'done' ? '#22c55e'
                    : condCalState === 'error' ? '#ef4444'
                    : condCalState === 'reading' ? '#FCD869'
                    : '#96EEF2',
                  cursor: condCalState === 'reading' ? 'not-allowed' : 'pointer',
                }"
                @click="readConductivityCalibration"
              >
                <Loader2 v-if="condCalState === 'reading'" class="w-4 h-4 animate-spin" />
                <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><path :d="mdiChip" /></svg>
                <span v-if="condCalState === 'reading'">Reading EEPROM…</span>
                <span v-else>Read Calibration from Probe</span>
              </button>
              <p v-if="condCalError" class="text-xs text-center" style="color: #f87171">{{ condCalError }}</p>

              <div v-if="condCal" class="rounded-lg overflow-hidden" style="border: 1px solid rgba(65, 185, 195, 0.2)">
                <div class="grid grid-cols-2 gap-px" style="background-color: rgba(65, 185, 195, 0.1)">
                  <div class="px-3 py-2" style="background-color: rgba(14, 36, 70, 0.7)">
                    <div class="text-xs" style="color: rgba(150, 238, 242, 0.5)">Serial #</div>
                    <div class="text-sm font-medium" style="color: #96EEF2">{{ condCal.serial_number }}</div>
                  </div>
                  <div class="px-3 py-2" style="background-color: rgba(14, 36, 70, 0.7)">
                    <div class="text-xs" style="color: rgba(150, 238, 242, 0.5)">Gain (raw)</div>
                    <div class="text-sm font-medium" style="color: #96EEF2">{{ condCal.gain.toExponential(4) }}</div>
                  </div>
                  <div class="px-3 py-2" style="background-color: rgba(14, 36, 70, 0.7)">
                    <div class="text-xs" style="color: rgba(150, 238, 242, 0.5)">CB</div>
                    <div class="text-sm font-medium" style="color: #96EEF2">{{ condCal.cal_cb.toExponential(4) }}</div>
                  </div>
                  <div class="px-3 py-2" style="background-color: rgba(14, 36, 70, 0.7)">
                    <div class="text-xs" style="color: rgba(150, 238, 242, 0.5)">CC</div>
                    <div class="text-sm font-medium" style="color: #96EEF2">{{ condCal.cal_cc.toExponential(4) }}</div>
                  </div>
                </div>
                <div class="px-3 py-2 text-xs font-mono leading-relaxed" style="background-color: rgba(0, 0, 0, 0.25); color: rgba(150, 238, 242, 0.8)">
                  <div style="color: rgba(150, 238, 242, 0.5)">Suggested extension settings:</div>
                  <div>DORIS_CONDUCTIVITY_GAIN={{ condCal.suggested_gain }}</div>
                  <div>DORIS_CONDUCTIVITY_FREQUENCY_HZ={{ condCal.suggested_frequency_hz }}</div>
                  <div>DORIS_CONDUCTIVITY_RANGE={{ condCal.suggested_range }}</div>
                </div>
              </div>
            </div>

            <!-- Other sensor types: calibration file upload -->
            <div v-else-if="selectedModule.type === 'sensor'" class="space-y-4">
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
