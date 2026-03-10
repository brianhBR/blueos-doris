/**
 * API composable for fetching data from the DORIS backend
 */
import { ref, readonly } from 'vue'

// API base URL - use relative path when served from the same origin
const API_BASE = '/api/v1'

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
  temperature: number
  uptime: string
}

export interface BatteryInfo {
  level: number
  voltage: number
  current: number
  temperature: number
  time_remaining: string
  charging: boolean
}

export interface StorageInfo {
  used_percent: number
  used_gb: number
  total_gb: number
  available_gb: number
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
}

// Note: useNetwork is deprecated, use useWifiNetworks instead
// This interface is kept for backwards compatibility but not actively used
export interface NetworkInfo {
  is_connected: boolean
  ssid: string | null
  signal_strength: number | null
  ip_address: string | null
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

async function fetchApi<T>(endpoint: string): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`)
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

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
      console.error('Failed to fetch system status:', e)
    } finally {
      loading.value = false
    }
  }

  return {
    status: readonly(status),
    loading: readonly(loading),
    error: readonly(error),
    fetchStatus
  }
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
      console.error('Failed to fetch battery:', e)
    } finally {
      loading.value = false
    }
  }

  return {
    battery: readonly(battery),
    loading: readonly(loading),
    error: readonly(error),
    fetchBattery
  }
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
      console.error('Failed to fetch storage:', e)
    } finally {
      loading.value = false
    }
  }

  return {
    storage: readonly(storage),
    loading: readonly(loading),
    error: readonly(error),
    fetchStorage
  }
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
      console.error('Failed to fetch location:', e)
    } finally {
      loading.value = false
    }
  }

  return {
    location: readonly(location),
    loading: readonly(loading),
    error: readonly(error),
    fetchLocation
  }
}

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
      console.error('Failed to fetch sensors:', e)
    } finally {
      loading.value = false
    }
  }

  return {
    modules: readonly(modules),
    loading: readonly(loading),
    error: readonly(error),
    fetchModules
  }
}

/**
 * @deprecated Use useWifiNetworks() instead for full network management
 */
export function useNetwork() {
  const network = ref<ConnectionStatus | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchNetwork() {
    loading.value = true
    error.value = null
    try {
      network.value = await fetchApi<ConnectionStatus>('/network/status')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch network'
      console.error('Failed to fetch network:', e)
    } finally {
      loading.value = false
    }
  }

  return {
    network: readonly(network),
    loading: readonly(loading),
    error: readonly(error),
    fetchNetwork
  }
}

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
      console.error('Failed to fetch networks:', e)
    } finally {
      loading.value = false
    }
  }

  async function scanNetworks() {
    scanning.value = true
    error.value = null
    try {
      networks.value = await fetchApi<WifiNetwork[]>('/network/scan')
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to scan networks'
      console.error('Failed to scan networks:', e)
    } finally {
      scanning.value = false
    }
  }

  async function connectToNetwork(ssid: string, password: string): Promise<boolean> {
    loading.value = true
    error.value = null
    try {
      const response = await fetch(`${API_BASE}/network/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ssid, password })
      })
      if (!response.ok) {
        throw new Error(`Connection failed: ${response.statusText}`)
      }
      const result = await response.json()
      connectionStatus.value = result
      return result.is_connected
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to connect'
      console.error('Failed to connect to network:', e)
      return false
    } finally {
      loading.value = false
    }
  }

  async function disconnectFromNetwork(): Promise<boolean> {
    loading.value = true
    error.value = null
    try {
      const response = await fetch(`${API_BASE}/network/disconnect`, {
        method: 'POST'
      })
      if (!response.ok) {
        throw new Error(`Disconnect failed: ${response.statusText}`)
      }
      const result = await response.json()
      connectionStatus.value = result
      return !result.is_connected
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to disconnect'
      console.error('Failed to disconnect from network:', e)
      return false
    } finally {
      loading.value = false
    }
  }

  return {
    networks: readonly(networks),
    connectionStatus: readonly(connectionStatus),
    loading: readonly(loading),
    scanning: readonly(scanning),
    error: readonly(error),
    fetchNetworks,
    scanNetworks,
    connectToNetwork,
    disconnectFromNetwork
  }
}

