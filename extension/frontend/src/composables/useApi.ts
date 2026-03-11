/**
 * API composables for fetching data from the DORIS backend.
 *
 * Backend API base: /api/v1
 * See backend OpenAPI spec at /openapi.json for full endpoint docs.
 */
import { ref, readonly } from 'vue'

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
  const response = await fetch(`${API_BASE}${endpoint}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
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

  async function deleteFile(fileId: string): Promise<boolean> {
    try {
      await deleteApi(`/media/files/${fileId}`)
      files.value = files.value.filter(f => f.id !== fileId)
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to delete file'
      return false
    }
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
    fetchSyncStatus,
    startSync,
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
