/**
 * API composables for fetching data from the DORIS backend.
 *
 * Backend API base: /api/v1
 * See backend OpenAPI spec at /openapi.json for full endpoint docs.
 */
import { ref, readonly, computed } from 'vue'

const API_BASE = '/api/v1'

// ── Types matching backend Pydantic models ──────────────────────────

export interface SystemStatus {
  connected: boolean
  battery_level: number
  battery_voltage: number
  battery_time_remaining: string
  storage_used_percent: number
  storage_used_gb: number
  storage_total_gb: number
  cpu_usage: number
  memory_usage: number
  temperature: number | null
  uptime: string
}

export interface BatteryInfo {
  level: number
  voltage: number | null
  current: number | null
  temperature: number | null
  time_remaining: string
  charging: boolean
}

export interface StorageInfo {
  total_gb: number
  used_gb: number
  available_gb: number
  used_percent: number
}

export interface LocationInfo {
  latitude: number
  longitude: number
  altitude: number
  depth: number
  heading: number
  speed: number
  satellites: number
  fix_type: string
  last_update: string
}

export interface SensorModule {
  id: string
  name: string
  type: string
  status: 'connected' | 'disconnected' | 'error'
  module_status: string
  last_reading: string | null
  power_usage: number
  sample_rate: number | null
  firmware_version: string | null
}

export interface SensorReading {
  sensor_id: string
  sensor_name: string
  value: number
  unit: string
  timestamp: string
  quality: number
}

export interface WifiNetwork {
  ssid: string
  signal_strength: number
  security: string
  frequency: string
  is_saved: boolean
  is_connected: boolean
}

export interface ConnectionStatus {
  is_connected: boolean
  ssid: string | null
  ip_address: string | null
  mac_address: string | null
  signal_strength: number | null
}

export interface NetworkFullInfo {
  connection: ConnectionStatus
  available_networks: WifiNetwork[]
  is_scanning: boolean
  serial_number: string | null
  hotspot_ssid: string | null
}

export interface MissionSummary {
  id: string
  name: string
  status: string
  date: string
  duration: string
  location: string | null
  max_depth: number | null
  image_count: number
  video_count: number
}

export interface MissionConfig {
  name: string
  start_trigger: {
    trigger_type: string
    value?: number | null
    scheduled_time?: string | null
    unit?: string | null
  }
  end_trigger: {
    trigger_type: string
    value?: number | null
    scheduled_time?: string | null
    unit?: string | null
  }
  timelapse_enabled: boolean
  timelapse_interval: number
  camera_settings: {
    resolution: string
    frame_rate: number
    focus: string
  }
  lighting_brightness: number
  sensors_enabled: string[]
}

export interface Mission {
  id: string
  name: string
  status: string
  config: MissionConfig
  created_at: string
  started_at: string | null
  completed_at: string | null
  duration_seconds: number | null
  location: string | null
  max_depth: number | null
  image_count: number
  video_count: number
}

export interface MediaFile {
  id: string
  filename: string
  media_type: 'image' | 'video' | 'data'
  size_bytes: number
  duration_seconds: number | null
  resolution: string | null
  created_at: string
  mission_id: string | null
  thumbnail_url: string | null
  download_url: string
  is_synced: boolean
}

export interface MediaMission {
  mission_id: string
  mission_name: string
  date: string
  image_count: number
  video_count: number
  data_file_count: number
  total_size_bytes: number
  thumbnail_url: string | null
}

export interface SyncStatus {
  is_syncing: boolean
  pending_files: number
  synced_files: number
  total_files: number
  last_sync: string | null
  error: string | null
}

// ── Configuration types ─────────────────────────────────────────────

export interface TimeValue {
  number: string
  unit: 'seconds' | 'minutes' | 'hours'
}

export interface CameraSettings {
  enabled: boolean
  camera_type: 'continuous-video' | 'timelapse' | 'video-interval'
  capture_frequency: number
  capture_frequency_unit: 'seconds' | 'minutes' | 'hours'
  video_record: TimeValue
  video_pause: TimeValue
  resolution: string
  image_type: string
  file_format: string
  video_file_format: string
  frame_rate: number
  focus: string
  iso: string
  white_balance: string
  exposure: string
  sharpness: string
  sleep_timer_enabled: boolean
  sleep_timer: TimeValue
}

export interface LightSettings {
  enabled: boolean
  mode: 'continuous' | 'interval'
  brightness: number
  match_camera_interval: boolean
  on_time: TimeValue
  off_time: TimeValue
}

export interface DescentPhase {
  camera: CameraSettings
  light: LightSettings
}

export interface BottomPhase {
  camera: CameraSettings
  camera_delay: TimeValue
  light: LightSettings
  light_delay: TimeValue
}

export interface ReleaseWeight {
  method: 'elapsed' | 'datetime'
  elapsed: TimeValue
  release_date: string
  release_time: string
}

export interface AscentPhase {
  same_as_descent: boolean
  camera: CameraSettings
  light: LightSettings
  release_weight: ReleaseWeight
}

export interface RecoverySettings {
  activate_mast_light: boolean
  update_frequency: string
  use_iridium: boolean
  use_lora: boolean
}

export interface DeploymentConfiguration {
  name: string
  dive_name: string
  estimated_depth: string
  descent: DescentPhase
  bottom: BottomPhase
  ascent: AscentPhase
  recovery: RecoverySettings
  created_at: string
  updated_at: string
}

export interface ConfigurationSummary {
  name: string
  created_at: string
  updated_at: string
}

// ── HTTP helpers ────────────────────────────────────────────────────

async function fetchApi<T>(endpoint: string, params?: Record<string, string>): Promise<T> {
  let url = `${API_BASE}${endpoint}`
  if (params) {
    const qs = new URLSearchParams(params).toString()
    url += `?${qs}`
  }
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

async function postApi<T>(endpoint: string, body?: unknown): Promise<T> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 30000)
  let response: Response
  try {
    response = await fetch(`${API_BASE}${endpoint}`, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
  } finally {
    clearTimeout(timeout)
  }
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

async function deleteApi<T>(endpoint: string): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, { method: 'DELETE' })
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

// ── System composables ──────────────────────────────────────────────

export function useSystemStatus() {
  const status = ref<SystemStatus | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchStatus() {
    loading.value = true
    error.value = null
    try {
      status.value = await fetchApi<SystemStatus>('/system/status')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch status'
    } finally {
      loading.value = false
    }
  }

  return { status: readonly(status), loading: readonly(loading), error: readonly(error), fetchStatus }
}

export function useBattery() {
  const battery = ref<BatteryInfo | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchBattery() {
    loading.value = true
    error.value = null
    try {
      battery.value = await fetchApi<BatteryInfo>('/system/battery')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch battery'
    } finally {
      loading.value = false
    }
  }

  return { battery: readonly(battery), loading: readonly(loading), error: readonly(error), fetchBattery }
}

export function useStorage() {
  const storage = ref<StorageInfo | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchStorage() {
    loading.value = true
    error.value = null
    try {
      storage.value = await fetchApi<StorageInfo>('/system/storage')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch storage'
    } finally {
      loading.value = false
    }
  }

  return { storage: readonly(storage), loading: readonly(loading), error: readonly(error), fetchStorage }
}

export function useLocation() {
  const location = ref<LocationInfo | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchLocation() {
    loading.value = true
    error.value = null
    try {
      location.value = await fetchApi<LocationInfo>('/system/location')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch location'
    } finally {
      loading.value = false
    }
  }

  return { location: readonly(location), loading: readonly(loading), error: readonly(error), fetchLocation }
}

// ── Sensor composables ──────────────────────────────────────────────

export function useSensors() {
  const modules = ref<SensorModule[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchModules() {
    loading.value = true
    error.value = null
    try {
      modules.value = await fetchApi<SensorModule[]>('/sensors/modules')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch sensors'
    } finally {
      loading.value = false
    }
  }

  async function fetchReadings(sensorId: string): Promise<SensorReading[]> {
    return await fetchApi<SensorReading[]>(`/sensors/${sensorId}/readings`)
  }

  async function updateConfig(sensorId: string, config: { sample_rate: number; enabled: boolean; calibration_file?: string | null }) {
    const response = await fetch(`${API_BASE}/sensors/${sensorId}/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sensor_id: sensorId, ...config }),
    })
    if (!response.ok) throw new Error(`Failed to update sensor config: ${response.statusText}`)
    return response.json()
  }

  return { modules: readonly(modules), loading: readonly(loading), error: readonly(error), fetchModules, fetchReadings, updateConfig }
}

// ── Network composables ─────────────────────────────────────────────

export function useWifiNetworks() {
  const networks = ref<WifiNetwork[]>([])
  const connectionStatus = ref<ConnectionStatus | null>(null)
  const serialNumber = ref<string | null>(null)
  const hotspotSsid = ref<string | null>(null)
  const loading = ref(false)
  const scanning = ref(false)
  const error = ref<string | null>(null)

  async function fetchNetworks() {
    loading.value = true
    error.value = null
    try {
      const info = await fetchApi<NetworkFullInfo>('/network')
      networks.value = info.available_networks
      connectionStatus.value = info.connection
      serialNumber.value = info.serial_number
      hotspotSsid.value = info.hotspot_ssid
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch networks'
    } finally {
      loading.value = false
    }
  }

  async function fetchStatus() {
    try {
      connectionStatus.value = await fetchApi<ConnectionStatus>('/network/status')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch status'
    }
  }

  async function scanNetworks() {
    scanning.value = true
    error.value = null
    try {
      networks.value = await fetchApi<WifiNetwork[]>('/network/scan')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to scan networks'
    } finally {
      scanning.value = false
    }
  }

  async function connectToNetwork(ssid: string, password: string): Promise<boolean> {
    loading.value = true
    error.value = null
    try {
      const result = await postApi<ConnectionStatus>('/network/connect', { ssid, password })
      connectionStatus.value = result
      return result.is_connected
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to connect'
      return false
    } finally {
      loading.value = false
    }
  }

  async function disconnectFromNetwork(): Promise<boolean> {
    loading.value = true
    error.value = null
    try {
      const result = await postApi<ConnectionStatus>('/network/disconnect')
      connectionStatus.value = result
      return !result.is_connected
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to disconnect'
      return false
    } finally {
      loading.value = false
    }
  }

  async function forgetNetwork(ssid: string): Promise<boolean> {
    try {
      await deleteApi(`/network/saved/${encodeURIComponent(ssid)}`)
      return true
    } catch {
      return false
    }
  }

  return {
    networks: readonly(networks),
    connectionStatus: readonly(connectionStatus),
    serialNumber: readonly(serialNumber),
    hotspotSsid: readonly(hotspotSsid),
    loading: readonly(loading),
    scanning: readonly(scanning),
    error: readonly(error),
    fetchNetworks,
    fetchStatus,
    scanNetworks,
    connectToNetwork,
    disconnectFromNetwork,
    forgetNetwork,
  }
}

// ── Mission composables ─────────────────────────────────────────────

export function useMissions() {
  const missions = ref<MissionSummary[]>([])
  const currentMission = ref<Mission | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchMissions() {
    loading.value = true
    error.value = null
    try {
      missions.value = await fetchApi<MissionSummary[]>('/missions')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch missions'
    } finally {
      loading.value = false
    }
  }

  async function fetchMission(id: string) {
    loading.value = true
    error.value = null
    try {
      currentMission.value = await fetchApi<Mission>(`/missions/${id}`)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch mission'
    } finally {
      loading.value = false
    }
  }

  async function createMission(config: MissionConfig): Promise<Mission | null> {
    loading.value = true
    error.value = null
    try {
      const mission = await postApi<Mission>('/missions', config)
      await fetchMissions()
      return mission
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to create mission'
      return null
    } finally {
      loading.value = false
    }
  }

  async function startMission(id: string): Promise<boolean> {
    try {
      await postApi(`/missions/${id}/start`)
      await fetchMissions()
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to start mission'
      return false
    }
  }

  async function stopMission(id: string): Promise<boolean> {
    try {
      await postApi(`/missions/${id}/stop`)
      await fetchMissions()
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to stop mission'
      return false
    }
  }

  async function deleteMission(id: string): Promise<boolean> {
    try {
      await deleteApi(`/missions/${id}`)
      missions.value = missions.value.filter(m => m.id !== id)
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to delete mission'
      return false
    }
  }

  return {
    missions: readonly(missions),
    currentMission: readonly(currentMission),
    loading: readonly(loading),
    error: readonly(error),
    fetchMissions,
    fetchMission,
    createMission,
    startMission,
    stopMission,
    deleteMission,
  }
}

// ── Media composables ───────────────────────────────────────────────

export function useMedia() {
  const files = ref<MediaFile[]>([])
  const mediaMissions = ref<MediaMission[]>([])
  const syncStatus = ref<SyncStatus | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchFiles(params?: { mission_id?: string; media_type?: string; search?: string }) {
    loading.value = true
    error.value = null
    try {
      const queryParams: Record<string, string> = {}
      if (params?.mission_id) queryParams.mission_id = params.mission_id
      if (params?.media_type) queryParams.media_type = params.media_type
      if (params?.search) queryParams.search = params.search
      files.value = await fetchApi<MediaFile[]>('/media/files', Object.keys(queryParams).length > 0 ? queryParams : undefined)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch media files'
    } finally {
      loading.value = false
    }
  }

  async function fetchMediaMissions() {
    loading.value = true
    error.value = null
    try {
      mediaMissions.value = await fetchApi<MediaMission[]>('/media/missions')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch media missions'
    } finally {
      loading.value = false
    }
  }

  async function deleteFile(filePath: string): Promise<boolean> {
    try {
      const params = new URLSearchParams({ path: filePath }).toString()
      const response = await fetch(`${API_BASE}/media/files?${params}`, { method: 'DELETE' })
      if (!response.ok) throw new Error(`Delete failed: ${response.statusText}`)
      files.value = files.value.filter(f => f.id !== filePath)
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to delete file'
      return false
    }
  }

  function downloadFile(filePath: string, fileName?: string) {
    const params = new URLSearchParams({ path: filePath }).toString()
    const url = `${API_BASE}/media/download?${params}`
    const a = document.createElement('a')
    a.href = url
    a.download = fileName || filePath.split('/').pop() || 'download'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }

  async function fetchSyncStatus() {
    try {
      syncStatus.value = await fetchApi<SyncStatus>('/media/sync/status')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch sync status'
    }
  }

  async function startSync(): Promise<boolean> {
    try {
      await postApi('/media/sync/start')
      await fetchSyncStatus()
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to start sync'
      return false
    }
  }

  return {
    files: readonly(files),
    mediaMissions: readonly(mediaMissions),
    syncStatus: readonly(syncStatus),
    loading: readonly(loading),
    error: readonly(error),
    fetchFiles,
    fetchMediaMissions,
    deleteFile,
    downloadFile,
    fetchSyncStatus,
    startSync,
  }
}

// ── Configuration composables ───────────────────────────────────────

export function useConfigurations() {
  const configurations = ref<ConfigurationSummary[]>([])
  const currentConfig = ref<DeploymentConfiguration | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  async function fetchConfigurations() {
    loading.value = true
    error.value = null
    try {
      configurations.value = await fetchApi<ConfigurationSummary[]>('/configurations')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch configurations'
    } finally {
      loading.value = false
    }
  }

  async function loadConfiguration(name: string): Promise<DeploymentConfiguration | null> {
    loading.value = true
    error.value = null
    try {
      currentConfig.value = await fetchApi<DeploymentConfiguration>(
        `/configurations/${encodeURIComponent(name)}`
      )
      return currentConfig.value
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to load configuration'
      return null
    } finally {
      loading.value = false
    }
  }

  async function saveConfiguration(config: DeploymentConfiguration): Promise<DeploymentConfiguration | null> {
    saving.value = true
    error.value = null
    try {
      const saved = await postApi<DeploymentConfiguration>('/configurations', config)
      currentConfig.value = saved
      await fetchConfigurations()
      return saved
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to save configuration'
      return null
    } finally {
      saving.value = false
    }
  }

  async function deleteConfiguration(name: string): Promise<boolean> {
    error.value = null
    try {
      await deleteApi(`/configurations/${encodeURIComponent(name)}`)
      configurations.value = configurations.value.filter(c => c.name !== name)
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to delete configuration'
      return false
    }
  }

  return {
    configurations: readonly(configurations),
    currentConfig: readonly(currentConfig),
    loading: readonly(loading),
    saving: readonly(saving),
    error: readonly(error),
    fetchConfigurations,
    loadConfiguration,
    saveConfiguration,
    deleteConfiguration,
  }
}

// ── Dive control composables ────────────────────────────────────────

export interface DiveStatus {
  param: string
  value: number | null
  active: boolean
}

export function useDiveControl() {
  const status = ref<DiveStatus | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function startDive(configurationName?: string, diveData?: Record<string, string>): Promise<boolean> {
    loading.value = true
    error.value = null
    try {
      const body: Record<string, unknown> = {}
      if (configurationName) body.configuration = configurationName
      if (diveData) Object.assign(body, diveData)
      const result = await postApi<{ success: boolean; message: string }>('/dive/start', Object.keys(body).length > 0 ? body : undefined)
      if (result.success) await fetchDiveStatus()
      return result.success
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to start dive'
      return false
    } finally {
      loading.value = false
    }
  }

  async function stopDive(): Promise<boolean> {
    loading.value = true
    error.value = null
    try {
      const result = await postApi<{ success: boolean; message: string }>('/dive/stop')
      if (result.success) await fetchDiveStatus()
      return result.success
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to stop dive'
      return false
    } finally {
      loading.value = false
    }
  }

  async function fetchDiveStatus() {
    try {
      status.value = await fetchApi<DiveStatus>('/dive/status')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch dive status'
    }
  }

  return {
    status: readonly(status),
    loading: readonly(loading),
    error: readonly(error),
    startDive,
    stopDive,
    fetchDiveStatus,
  }
}

// ── Artemis composables ─────────────────────────────────────────────

export interface SerialPortInfo {
  device: string
  description: string
  hwid: string
}

export interface FirmwareUploadResult {
  path: string
  size_bytes: number
}

export function useArtemis() {
  const ports = ref<SerialPortInfo[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchPorts() {
    loading.value = true
    error.value = null
    try {
      ports.value = await fetchApi<SerialPortInfo[]>('/artemis/ports')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch serial ports'
    } finally {
      loading.value = false
    }
  }

  async function uploadFirmware(file: File): Promise<FirmwareUploadResult | null> {
    loading.value = true
    error.value = null
    try {
      const response = await fetch(
        `${API_BASE}/artemis/firmware/upload?filename=${encodeURIComponent(file.name)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/octet-stream' },
          body: file,
        },
      )
      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`
        try {
          const body = await response.json()
          if (body?.error) detail = body.error
        } catch { /* no JSON body */ }
        throw new Error(`Firmware upload failed: ${detail}`)
      }
      return await response.json() as FirmwareUploadResult
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to upload firmware'
      return null
    } finally {
      loading.value = false
    }
  }

  return {
    ports: readonly(ports),
    loading: readonly(loading),
    error: readonly(error),
    fetchPorts,
    uploadFirmware,
  }
}

export interface FlashStatusResponse {
  session_id: string
  lines: string[]
  total_lines: number
  done: boolean
  success: boolean
  error: string | null
}

export interface ArtemisFlashResult {
  success: boolean
  message: string
}

export function useArtemisFlash() {
  const flashing = ref(false)
  const progress = ref<string[]>([])
  const result = ref<ArtemisFlashResult | null>(null)
  const error = ref<string | null>(null)

  let pollTimer: number | undefined
  let lineIndex = 0

  async function startFlash(params: {
    port: string
    firmware_path: string
    baud?: number
    timeout?: number
  }) {
    progress.value = []
    result.value = null
    error.value = null
    flashing.value = true
    lineIndex = 0

    try {
      const response = await fetch(`${API_BASE}/artemis/flash`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          port: params.port,
          firmware_path: params.firmware_path,
          baud: params.baud ?? 115200,
          timeout: params.timeout ?? 0.5,
        }),
      })
      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`
        try {
          const body = await response.json()
          if (body?.error) detail = body.error
        } catch { /* no JSON body */ }
        throw new Error(`Failed to start flash: ${detail}`)
      }
      const { session_id } = await response.json() as { session_id: string }
      pollProgress(session_id)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to start flash'
      flashing.value = false
    }
  }

  function pollProgress(sessionId: string) {
    pollTimer = window.setInterval(async () => {
      try {
        const status = await fetchApi<FlashStatusResponse>(
          `/artemis/flash/status?session_id=${sessionId}&from_line=${lineIndex}`
        )
        if (status.lines.length > 0) {
          progress.value = [...progress.value, ...status.lines]
          lineIndex = status.total_lines
        }
        if (status.done) {
          stopPolling()
          flashing.value = false
          result.value = {
            success: status.success,
            message: status.success ? 'Upload Successful' : (status.error || 'Upload Failed'),
          }
        }
      } catch (e) {
        stopPolling()
        error.value = e instanceof Error ? e.message : 'Failed to poll flash status'
        flashing.value = false
      }
    }, 500)
  }

  function stopPolling() {
    if (pollTimer !== undefined) {
      clearInterval(pollTimer)
      pollTimer = undefined
    }
  }

  function reset() {
    stopPolling()
    flashing.value = false
    progress.value = []
    result.value = null
    error.value = null
    lineIndex = 0
  }

  return {
    flashing: readonly(flashing),
    progress: readonly(progress),
    result: readonly(result),
    error: readonly(error),
    startFlash,
    reset,
    stopPolling,
  }
}

// ── Notification types ──────────────────────────────────────────────

export interface NotificationItemApi {
  id: string
  type: 'info' | 'warning' | 'success' | 'error'
  category: 'mission' | 'system' | 'network' | 'software'
  title: string
  message: string
  timestamp: string
  read: boolean
  link_to: string | null
}

export interface NotificationSettingsApi {
  mission_alerts: boolean
  system_warnings: boolean
  network_status: boolean
  software_updates: boolean
}

// ── Notification composables ────────────────────────────────────────

export function useNotifications() {
  const notifications = ref<NotificationItemApi[]>([])
  const unreadCount = ref(0)
  const settings = ref<NotificationSettingsApi | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchNotifications() {
    loading.value = true
    error.value = null
    try {
      notifications.value = await fetchApi<NotificationItemApi[]>('/notifications')
      unreadCount.value = notifications.value.filter(n => !n.read).length
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch notifications'
    } finally {
      loading.value = false
    }
  }

  async function fetchUnreadCount() {
    try {
      const result = await fetchApi<{ count: number }>('/notifications/unread-count')
      unreadCount.value = result.count
    } catch {
      // silently ignore badge poll failures
    }
  }

  async function markAsRead(id: string): Promise<boolean> {
    try {
      await postApi<{ success: boolean }>(`/notifications/${encodeURIComponent(id)}/read`)
      notifications.value = notifications.value.map(n =>
        n.id === id ? { ...n, read: true } : n
      )
      unreadCount.value = notifications.value.filter(n => !n.read).length
      return true
    } catch {
      return false
    }
  }

  async function markAllRead(): Promise<boolean> {
    try {
      await postApi<{ success: boolean }>('/notifications/read-all')
      notifications.value = notifications.value.map(n => ({ ...n, read: true }))
      unreadCount.value = 0
      return true
    } catch {
      return false
    }
  }

  async function deleteNotification(id: string): Promise<boolean> {
    try {
      await deleteApi<{ success: boolean }>(`/notifications/${encodeURIComponent(id)}`)
      notifications.value = notifications.value.filter(n => n.id !== id)
      unreadCount.value = notifications.value.filter(n => !n.read).length
      return true
    } catch {
      return false
    }
  }

  async function fetchSettings() {
    try {
      settings.value = await fetchApi<NotificationSettingsApi>('/notifications/settings')
    } catch {
      // use defaults
    }
  }

  async function updateSettings(newSettings: NotificationSettingsApi): Promise<boolean> {
    try {
      const response = await fetch(`${API_BASE}/notifications/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newSettings),
      })
      if (!response.ok) return false
      settings.value = await response.json()
      return true
    } catch {
      return false
    }
  }

  return {
    notifications: readonly(notifications),
    unreadCount: readonly(unreadCount),
    settings: readonly(settings),
    loading: readonly(loading),
    error: readonly(error),
    fetchNotifications,
    fetchUnreadCount,
    markAsRead,
    markAllRead,
    deleteNotification,
    fetchSettings,
    updateSettings,
  }
}

// ── Storage migration status ────────────────────────────────────────

export interface StorageMigrationStatus {
  state: 'idle' | 'checking' | 'mounting' | 'configuring' | 'done' | 'skipped' | 'error'
  message: string
  error: string
}

export function useStorageMigration() {
  const status = ref<StorageMigrationStatus | null>(null)

  async function fetchMigrationStatus() {
    try {
      status.value = await fetchApi<StorageMigrationStatus>('/system/storage/migration')
    } catch {
      // backend not reachable yet, ignore
    }
  }

  const isActive = computed(() => {
    if (!status.value) return false
    return ['checking', 'mounting', 'configuring'].includes(status.value.state)
  })

  const isError = computed(() => status.value?.state === 'error')

  return {
    status: readonly(status),
    isActive: readonly(isActive),
    isError: readonly(isError),
    fetchMigrationStatus,
  }
}

// ── Health check ────────────────────────────────────────────────────

export async function checkHealth(): Promise<boolean> {
  try {
    await fetchApi('/health')
    return true
  } catch {
    return false
  }
}
