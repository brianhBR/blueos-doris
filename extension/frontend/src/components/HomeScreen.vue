<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { Battery, HardDrive, Satellite, Activity, AlertTriangle, CheckCircle, Compass, Database, Gauge } from 'lucide-vue-next'
import type { Screen } from '../types'
import { useSystemStatus, useBattery, useStorage, useLocation, useSensors } from '../composables/useApi'

interface Props {
  isConnected: boolean
}

defineProps<Props>()

const emit = defineEmits<{
  navigate: [screen: Screen]
}>()

// API composables
const { status: systemStatus, fetchStatus } = useSystemStatus()
const { battery, fetchBattery } = useBattery()
const { storage, fetchStorage } = useStorage()
const { location, fetchLocation } = useLocation()
const { modules: sensorModules, fetchModules } = useSensors()

// Computed values from API data
const batteryLevel = computed(() => battery.value?.level ?? systemStatus.value?.battery_level ?? 0)
const storageUsed = computed(() => storage.value?.used_percent ?? systemStatus.value?.storage_used_percent ?? 0)
const storageAvailable = computed(() => {
  if (storage.value?.available_gb) return storage.value.available_gb
  // Calculate from system status if available
  if (systemStatus.value?.storage_total_gb && systemStatus.value?.storage_used_gb) {
    return systemStatus.value.storage_total_gb - systemStatus.value.storage_used_gb
  }
  return 0
})
const storageTotal = computed(() => storage.value?.total_gb ?? systemStatus.value?.storage_total_gb ?? 0)
const batteryTimeRemaining = computed(() => battery.value?.time_remaining ?? systemStatus.value?.battery_time_remaining ?? 'Unknown')

const gpsStatus = computed<'active' | 'searching' | 'inactive'>(() => {
  if (!location.value) return 'inactive'
  if (location.value.fix_type === 'none') return 'searching'
  return 'active'
})

const locationDisplay = computed(() => {
  if (!location.value) return { lat: 'N/A', lon: 'N/A', lastUpdate: 'Never' }
  return {
    lat: `${Math.abs(location.value.latitude).toFixed(4)}° ${location.value.latitude >= 0 ? 'N' : 'S'}`,
    lon: `${Math.abs(location.value.longitude).toFixed(4)}° ${location.value.longitude >= 0 ? 'E' : 'W'}`,
    lastUpdate: location.value.last_update
  }
})

// Transform sensor modules for display
const modules = computed(() => {
  return sensorModules.value.map((m, index) => ({
    id: index,
    name: m.name,
    status: m.status as 'connected' | 'disconnected',
    moduleStatus: m.module_status
  }))
})

// Fetch all data
async function refreshData() {
  await Promise.all([
    fetchStatus(),
    fetchBattery(),
    fetchStorage(),
    fetchLocation(),
    fetchModules()
  ])
}

// Refresh timer
let refreshInterval: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  refreshData()
  // Auto-refresh every 5 seconds
  refreshInterval = setInterval(refreshData, 5000)
})

onUnmounted(() => {
  if (refreshInterval) {
    clearInterval(refreshInterval)
  }
})

const getStatusStyle = (status: string) => {
  if (status === 'active') return { backgroundColor: 'rgba(252, 216, 105, 0.2)', color: '#FCD869' }
  if (status === 'searching') return { backgroundColor: 'rgba(255, 153, 55, 0.2)', color: '#FF9937' }
  return { backgroundColor: 'rgba(221, 44, 29, 0.2)', color: '#DD2C1D' }
}

const getModuleStatusColor = (moduleStatus: string | undefined) => {
  if (!moduleStatus) return '#96EEF2'
  if (moduleStatus.includes('Warning')) return '#FF9937'
  if (moduleStatus.includes('Disconnected') || moduleStatus.includes('error')) return '#DD2C1D'
  return '#FCD869'
}

const getConnectionStyle = (status: string) => {
  if (status === 'connected') return { backgroundColor: 'rgba(252, 216, 105, 0.2)', color: '#FCD869' }
  return { backgroundColor: 'rgba(221, 44, 29, 0.2)', color: '#DD2C1D' }
}
</script>

<template>
  <div class="max-w-7xl mx-auto px-4 py-6 md:py-8">
    <!-- Welcome Header -->
    <div class="text-center mb-8">
      <h1 class="text-white text-4xl md:text-5xl mb-2 font-bold">DORIS</h1>
      <p style="color: #96EEF2; font-family: 'Montserrat', sans-serif">Deep Ocean Research and Imaging System</p>
    </div>

    <!-- System Status Cards -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
      <!-- Battery Status -->
      <div
        class="backdrop-blur-sm rounded-xl p-6 border"
        style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
      >
        <div class="flex items-center justify-between mb-4">
          <div class="flex items-center gap-2">
            <Battery class="w-5 h-5" style="color: #96EEF2" />
            <span class="text-white">Battery Level</span>
          </div>
          <span style="color: #FCD869">{{ batteryLevel }}%</span>
        </div>
        <div class="w-full rounded-full h-2" style="background-color: rgba(14, 36, 70, 0.6)">
          <div
            class="h-2 rounded-full transition-all"
            :style="{
              width: `${batteryLevel}%`,
              background: 'linear-gradient(90deg, #FCD869 0%, #41B9C3 100%)'
            }"
          ></div>
        </div>
        <p class="text-sm mt-2" style="color: #96EEF2">Estimated: {{ batteryTimeRemaining }} remaining</p>
      </div>

      <!-- Storage Status -->
      <div
        class="backdrop-blur-sm rounded-xl p-6 border"
        style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
      >
        <div class="flex items-center justify-between mb-4">
          <div class="flex items-center gap-2">
            <HardDrive class="w-5 h-5" style="color: #96EEF2" />
            <span class="text-white">Device Storage</span>
          </div>
          <span style="color: #FCD869">{{ storageUsed.toFixed(1) }}%</span>
        </div>
        <div class="w-full rounded-full h-2" style="background-color: rgba(14, 36, 70, 0.6)">
          <div
            class="h-2 rounded-full transition-all"
            :style="{
              width: `${storageUsed}%`,
              background: 'linear-gradient(90deg, #41B9C3 0%, #96EEF2 100%)'
            }"
          ></div>
        </div>
        <p class="text-sm mt-2" style="color: #96EEF2">{{ storageAvailable.toFixed(0) }} GB available of {{ storageTotal.toFixed(0) }} GB</p>
        <button
          @click="emit('navigate', 'media')"
          class="w-full mt-4 px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 text-sm"
          style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
        >
          View Media Files
        </button>
      </div>

      <!-- Location -->
      <div
        class="backdrop-blur-sm rounded-xl p-6 border"
        style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
      >
        <div class="flex items-center justify-between mb-4">
          <div class="flex items-center gap-2">
            <Satellite class="w-5 h-5" style="color: #96EEF2" />
            <span class="text-white">Location</span>
          </div>
          <span class="text-sm px-2 py-1 rounded" :style="getStatusStyle(gpsStatus)">
            {{ gpsStatus === 'active' ? 'Active' : gpsStatus === 'searching' ? 'Searching' : 'Inactive' }}
          </span>
        </div>
        <p class="text-sm" style="color: #96EEF2">Last Position: {{ locationDisplay.lat }}, {{ locationDisplay.lon }}</p>
        <p class="text-sm mt-1" style="color: #96EEF2">Updated: {{ locationDisplay.lastUpdate }}</p>
        <button
          @click="emit('navigate', 'location')"
          class="w-full mt-4 px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 text-sm"
          style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
        >
          View Location Details
        </button>
      </div>
    </div>

    <!-- DORIS Visualization -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
      <!-- 3D Model Placeholder -->
      <div
        class="backdrop-blur-sm rounded-xl p-6 border"
        style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
      >
        <h2 class="text-white mb-4 flex items-center gap-2 text-2xl" style="font-family: 'Montserrat', sans-serif">
          <Activity class="w-5 h-5" style="color: #96EEF2" />
          System Overview
        </h2>
        <div
          class="aspect-video rounded-lg flex items-center justify-center border"
          style="background: linear-gradient(135deg, rgba(0, 77, 100, 0.4) 0%, rgba(14, 36, 70, 0.6) 100%); border-color: rgba(65, 185, 195, 0.2)"
        >
          <div class="text-center">
            <div
              class="w-32 h-32 mx-auto mb-4 rounded-full flex items-center justify-center"
              style="background: linear-gradient(135deg, #41B9C3 0%, #96EEF2 100%)"
            >
              <div class="w-24 h-24 rounded-full border-4 flex items-center justify-center" style="border-color: rgba(255, 255, 255, 0.3)">
                <div class="w-16 h-16 rounded-full" style="background-color: rgba(150, 238, 242, 0.3)"></div>
              </div>
            </div>
            <p style="color: #96EEF2">3D DORIS Model</p>
            <p class="text-sm" style="color: #41B9C3">(Interactive visualization)</p>
          </div>
        </div>
      </div>

      <!-- Module Status -->
      <div
        class="backdrop-blur-sm rounded-xl p-6 border"
        style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
      >
        <h2 class="text-white mb-4 flex items-center gap-2 text-2xl" style="font-family: 'Montserrat', sans-serif">
          <Activity class="w-5 h-5" style="color: #96EEF2" />
          Connected Modules
        </h2>
        <!-- Table Header -->
        <div class="grid grid-cols-3 gap-4 px-4 pb-2 mb-2 border-b" style="border-color: rgba(65, 185, 195, 0.3)">
          <div class="text-sm" style="color: #96EEF2">Module</div>
          <div class="text-sm text-center" style="color: #96EEF2">Status</div>
          <div class="text-sm text-right" style="color: #96EEF2">Connection</div>
        </div>
        <div class="space-y-2">
          <div
            v-for="module in modules"
            :key="module.id"
            class="rounded-lg p-4 grid grid-cols-3 gap-4 items-center"
            style="background-color: rgba(14, 36, 70, 0.5)"
          >
            <div class="flex items-center gap-3">
              <CheckCircle v-if="module.status === 'connected'" class="w-5 h-5" style="color: #FCD869" />
              <AlertTriangle v-else class="w-5 h-5" style="color: #DD2C1D" />
              <p class="text-white">{{ module.name }}</p>
            </div>
            <div class="flex justify-center">
              <span class="text-sm" :style="{ color: getModuleStatusColor(module.moduleStatus) }">
                {{ module.moduleStatus }}
              </span>
            </div>
            <div class="flex justify-end">
              <div class="px-3 py-1 rounded text-sm" :style="getConnectionStyle(module.status)">
                {{ module.status === 'connected' ? 'Connected' : 'Not Connected' }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Quick Actions -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <button
        @click="emit('navigate', 'missions')"
        class="text-white rounded-xl p-6 transition-all hover:opacity-90"
        style="background: linear-gradient(135deg, #FF9937 0%, #DD7A28 100%)"
      >
        <Compass class="w-8 h-8 mb-2 mx-auto" />
        <h3 class="mb-1">New Mission</h3>
        <p class="text-sm opacity-90">Configure your next deployment</p>
      </button>

      <button
        @click="emit('navigate', 'media')"
        class="text-white rounded-xl p-6 transition-all hover:opacity-90"
        style="background: linear-gradient(135deg, #187D8B 0%, #004D64 100%)"
      >
        <Database class="w-8 h-8 mb-2 mx-auto" />
        <h3 class="mb-1">View Media</h3>
        <p class="text-sm opacity-90">Access captured data and videos</p>
      </button>

      <button
        @click="emit('navigate', 'sensors')"
        class="text-white rounded-xl p-6 transition-all hover:opacity-90"
        style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
      >
        <Gauge class="w-8 h-8 mb-2 mx-auto" />
        <h3 class="mb-1">Configure Sensors</h3>
        <p class="text-sm opacity-90">Manage modules and settings</p>
      </button>
    </div>
  </div>
</template>

