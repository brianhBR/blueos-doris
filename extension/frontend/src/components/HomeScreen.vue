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
import { useSystemStatus, useBattery, useStorage, useLocation, useSensors } from '../composables/useApi'
import type { SensorModule } from '../composables/useApi'

const mdiCompassOutline = 'M7,17L10.2,10.2L17,7L13.8,13.8L7,17M12,11.1A0.9,0.9 0 0,0 11.1,12A0.9,0.9 0 0,0 12,12.9A0.9,0.9 0 0,0 12.9,12A0.9,0.9 0 0,0 12,11.1M12,2A10,10 0 0,1 22,12A10,10 0 0,1 12,22A10,10 0 0,1 2,12A10,10 0 0,1 12,2M12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20A8,8 0 0,0 20,12A8,8 0 0,0 12,4Z'
import type { Screen } from '../types'

const props = defineProps<{
  isConnected: boolean
  releaseWeightBy: 'datetime' | 'elapsed'
}>()

const emit = defineEmits<{
  navigate: [screen: Screen, sensorName?: string]
  startDive: []
  'update:releaseWeightBy': [value: 'datetime' | 'elapsed']
  configurationSelect: [config: string]
}>()

const { status: systemStatus, fetchStatus } = useSystemStatus()
const { battery, fetchBattery } = useBattery()
const { storage, fetchStorage } = useStorage()
const { location, fetchLocation } = useLocation()
const { modules: sensorModules, fetchModules } = useSensors()

const batteryLevel = computed(() => battery.value?.level ?? systemStatus.value?.battery_level ?? 0)
const storageUsed = computed(() => storage.value?.used_percent ?? systemStatus.value?.storage_used_percent ?? 0)
const storageTotal = computed(() => storage.value?.total_gb ?? systemStatus.value?.storage_total_gb ?? 100)
const storageAvailableGb = computed(() => storage.value?.available_gb ?? (storageTotal.value - (storage.value?.used_gb ?? systemStatus.value?.storage_used_gb ?? 0)))
const batteryTimeRemaining = computed(() => battery.value?.time_remaining ?? systemStatus.value?.battery_time_remaining ?? 'Unknown')

const gpsStatus = computed<'active' | 'searching' | 'inactive'>(() => {
  if (!location.value) return 'inactive'
  if (location.value.fix_type === 'none') return 'inactive'
  if (location.value.satellites > 0) return 'active'
  return 'searching'
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

let pollInterval: number | undefined

onMounted(() => {
  fetchStatus()
  fetchBattery()
  fetchStorage()
  fetchLocation()
  fetchModules()
  pollInterval = setInterval(() => {
    fetchStatus()
    fetchBattery()
    fetchStorage()
    fetchLocation()
    fetchModules()
  }, 5000) as unknown as number
})

onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval)
})

const diveName = ref('')
const username = ref('')
const selectedConfiguration = ref('')
const estimatedDepth = ref('')
const estimatedBottomTime = ref('')
const releaseWeightDate = ref('2026-02-07')
const releaseWeightTime = ref('12:00')
const sortColumn = ref<'sensor' | 'status' | null>(null)
const sortDirection = ref<'asc' | 'desc'>('asc')
const leakDetected = ref(true)
const isCheckingLeaks = ref(false)

const previousUsernames = ['Captain Smith', 'Dr. Johnson', 'Prof. Lee', 'Researcher Chen']

const savedConfigurations = [
  'DORIS 24 Hour Dive Configuration',
  'DORIS 12 Hour Dive Configuration',
  'DORIS 6 Hour Dive Configuration',
  'DORIS 4 Hour Dive Configuration',
  'Release Date Time Test',
  'My Saved Configuration 1',
  'My Saved Configuration 2',
  'My Saved Configuration 3',
  'My Saved Configuration 4',
  'My Saved Configuration 5'
]

const configurationElapsedTimes: Record<string, number> = {
  'DORIS 24 Hour Dive Configuration': 24,
  'DORIS 12 Hour Dive Configuration': 12,
  'DORIS 6 Hour Dive Configuration': 6,
  'DORIS 4 Hour Dive Configuration': 4,
  'Release Date Time Test': 0,
  'My Saved Configuration 1': 8,
  'My Saved Configuration 2': 10,
  'My Saved Configuration 3': 12,
  'My Saved Configuration 4': 6,
  'My Saved Configuration 5': 4
}

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

  const descentAscentRate = 10
  const descentTimeMinutes = depth / descentAscentRate
  const ascentTimeMinutes = depth / descentAscentRate
  const descentTimeHours = descentTimeMinutes / 60
  const ascentTimeHours = ascentTimeMinutes / 60

  let bottomTimeHours = 0
  let totalDiveTimeHours = 0

  if (props.releaseWeightBy === 'datetime') {
    if (estimatedBottomTime.value) {
      bottomTimeHours = parseFloat(estimatedBottomTime.value)
      if (isNaN(bottomTimeHours)) bottomTimeHours = 0
    }
    totalDiveTimeHours = descentTimeHours + bottomTimeHours + ascentTimeHours
  } else {
    const elapsedTime = configurationElapsedTimes[selectedConfiguration.value] || 0
    totalDiveTimeHours = elapsedTime
    bottomTimeHours = Math.max(0, totalDiveTimeHours - descentTimeHours - ascentTimeHours)
  }

  const basePower = 2
  const cameraPower = 5
  const lightPower = 10
  const dataPower = 1.5
  let totalPowerDraw = basePower + dataPower
  const cameraOnHours = bottomTimeHours
  const lightOnHours = bottomTimeHours
  const cameraAveragePower = totalDiveTimeHours > 0 ? (cameraPower * cameraOnHours) / totalDiveTimeHours : 0
  const lightAveragePower = totalDiveTimeHours > 0 ? (lightPower * lightOnHours) / totalDiveTimeHours : 0
  totalPowerDraw += cameraAveragePower + lightAveragePower

  const batteryCapacity = 100
  const batteryLifeHours = totalPowerDraw > 0 ? batteryCapacity / totalPowerDraw : 0
  const batteryUsagePercent = Math.min(
    batteryLifeHours > 0 ? (totalDiveTimeHours / batteryLifeHours) * 100 : 100,
    100
  )
  const batteryRemainingPercent = Math.max(0, 100 - batteryUsagePercent)
  const batteryOk = batteryRemainingPercent >= 20

  const storagePerHour = 8
  const estimatedStorageNeeded = totalDiveTimeHours * storagePerHour
  const storageAvailable = 100 - storageUsed.value
  const storageRemaining = storageAvailable - estimatedStorageNeeded
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
    descentTimeHours: descentTimeHours.toFixed(1),
    bottomTimeHours: bottomTimeHours.toFixed(1),
    ascentTimeHours: ascentTimeHours.toFixed(1),
    batteryUsagePercent: Math.round(batteryUsagePercent),
    estimatedStorageNeeded: Math.round(estimatedStorageNeeded),
    totalPowerDraw: totalPowerDraw.toFixed(1),
    batteryLifeHours: batteryLifeHours.toFixed(1),
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

const handleConfigurationChange = () => {
  if (selectedConfiguration.value === '__new__') {
    selectedConfiguration.value = ''
    emit('navigate', 'dives')
    return
  }
  if (selectedConfiguration.value) {
    emit('configurationSelect', selectedConfiguration.value)
  }
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
          <label class="block text-sm mb-2" :style="{ color: releaseWeightBy === 'datetime' ? '#96EEF2' : 'rgba(150, 238, 242, 0.5)' }">Release Weight Date (DD-MM-YYYY)</label>
          <input
            v-model="releaseWeightDate"
            type="date"
            :disabled="releaseWeightBy !== 'datetime'"
            class="w-full rounded-lg px-4 py-3 text-white outline-none disabled:cursor-not-allowed"
            :style="{
              backgroundColor: releaseWeightBy === 'datetime' ? 'rgba(14, 36, 70, 0.5)' : 'rgba(14, 36, 70, 0.3)',
              border: '1px solid rgba(65, 185, 195, 0.3)',
              colorScheme: 'dark',
              opacity: releaseWeightBy === 'datetime' ? 1 : 0.5
            }"
          />
          <p v-if="releaseWeightBy !== 'datetime'" class="text-xs mt-1" style="color: rgba(150, 238, 242, 0.7)">
            Set Release by Date/Time in Configuration
          </p>
        </div>

        <div>
          <label class="block text-sm mb-2" :style="{ color: releaseWeightBy === 'datetime' ? '#96EEF2' : 'rgba(150, 238, 242, 0.5)' }">Release Weight Time (UTC)</label>
          <input
            v-model="releaseWeightTime"
            type="time"
            step="60"
            :disabled="releaseWeightBy !== 'datetime'"
            class="w-full rounded-lg px-4 py-3 text-white outline-none disabled:cursor-not-allowed"
            :style="{
              backgroundColor: releaseWeightBy === 'datetime' ? 'rgba(14, 36, 70, 0.5)' : 'rgba(14, 36, 70, 0.3)',
              border: '1px solid rgba(65, 185, 195, 0.3)',
              colorScheme: 'dark',
              opacity: releaseWeightBy === 'datetime' ? 1 : 0.5
            }"
          />
          <p v-if="releaseWeightBy !== 'datetime'" class="text-xs mt-1" style="color: rgba(150, 238, 242, 0.7)">
            Set Release by Date/Time in Configuration
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

        <div class="flex items-end">
          <button
            @click="emit('startDive')"
            class="w-full px-6 py-3 rounded-lg text-white transition-all hover:opacity-90"
            style="background-color: #FF9937"
          >
            Start Dive
          </button>
        </div>
      </div>

      <p class="text-sm" style="color: #96EEF2">
        NOTE: Dive starts when device detects it is at X meters.
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
          <p class="text-xs mb-1" style="color: #96EEF2">Power Draw</p>
          <p class="text-xl font-bold text-white">{{ diveFeasibility.totalPowerDraw }} W</p>
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
            <span class="text-white">Battery</span>
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
          />
        </div>
        <p class="text-sm mt-2" style="color: #96EEF2">Estimated: {{ batteryTimeRemaining }} remaining</p>
      </div>

      <!-- Storage Available -->
      <div class="card-bg rounded-xl p-6">
        <div class="flex items-center justify-between mb-4">
          <div class="flex items-center gap-2">
            <HardDrive class="w-5 h-5" style="color: #96EEF2" />
            <span class="text-white">Storage Available</span>
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
        <p class="text-sm" style="color: #96EEF2">Use the LoRa locator device or your Iridium app to track DORIS location.</p>
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
          class="aspect-[3/4] rounded-lg flex items-center justify-center"
          style="background: linear-gradient(135deg, rgba(0, 77, 100, 0.4) 0%, rgba(14, 36, 70, 0.6) 100%); border: 1px solid rgba(65, 185, 195, 0.2)"
        >
          <div class="text-center">
            <div
              class="w-24 h-24 mx-auto mb-4 rounded-full flex items-center justify-center"
              style="background: linear-gradient(135deg, #41B9C3 0%, #96EEF2 100%)"
            >
              <div
                class="rounded-full border-4 flex items-center justify-center"
                style="border-color: rgba(255, 255, 255, 0.3); width: 72px; height: 72px"
              >
                <div
                  class="w-12 h-12 rounded-full"
                  style="background-color: rgba(150, 238, 242, 0.3)"
                />
              </div>
            </div>
            <p style="color: #96EEF2">3D DORIS Model</p>
            <p class="text-xs" style="color: #41B9C3">(Interactive visualization)</p>
          </div>
        </div>
      </div>

      <!-- Connected Sensors (3 cols) -->
      <div class="lg:col-span-3 card-bg rounded-xl p-6">
        <h2
          class="text-white mb-4 flex items-center gap-2 text-xl"
          style="font-family: 'Montserrat', sans-serif"
        >
          <Activity class="w-5 h-5" style="color: #96EEF2" />
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

        <!-- Table Rows -->
        <div class="space-y-2">
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
