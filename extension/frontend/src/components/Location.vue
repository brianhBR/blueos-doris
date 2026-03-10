<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { MapPin, Satellite, Radio, Navigation, Clock, Signal } from 'lucide-vue-next'
import { useLocation } from '../composables/useApi'

// Fetch real location data from API
const { location, fetchLocation } = useLocation()

const iridiumStatus = ref<'active' | 'standby' | 'offline'>('active')
const loraStatus = ref<'active' | 'standby' | 'offline'>('active')

// Computed location values from API
const currentLocation = computed(() => ({
  lat: location.value?.latitude ?? 0,
  lon: location.value?.longitude ?? 0,
  altitude: location.value?.altitude ?? 0,
  depth: location.value?.depth ?? 0,
  lastUpdate: location.value?.last_update ?? 'Unknown'
}))

// GPS/Satellite data from API
const iridiumData = computed(() => ({
  signalStrength: 85, // Mock - no Iridium data in BlueOS
  satellites: location.value?.satellites ?? 0,
  lastTransmission: '5 minutes ago', // Mock
  nextScheduled: '10 minutes' // Mock
}))

// Fetch data on mount and refresh periodically
let refreshInterval: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  fetchLocation()
  refreshInterval = setInterval(fetchLocation, 5000)
})

onUnmounted(() => {
  if (refreshInterval) clearInterval(refreshInterval)
})

const loraData = {
  signalStrength: 72,
  range: '2.3 km',
  baseStation: 'Research Vessel Alpha',
  lastPing: '30 seconds ago'
}

const getStatusStyle = (status: string) => {
  if (status === 'active') return { backgroundColor: 'rgba(252, 216, 105, 0.2)', color: '#FCD869' }
  if (status === 'standby') return { backgroundColor: 'rgba(255, 153, 55, 0.2)', color: '#FF9937' }
  return { backgroundColor: 'rgba(221, 44, 29, 0.2)', color: '#DD2C1D' }
}

const getStatusLabel = (status: string) => {
  if (status === 'active') return 'Active'
  if (status === 'standby') return 'Standby'
  return 'Offline'
}
</script>

<template>
  <div class="max-w-7xl mx-auto px-4 py-6 md:py-8">
    <!-- Header -->
    <div class="mb-6">
      <h1 class="text-white text-2xl md:text-3xl mb-2 flex items-center gap-2">
        <MapPin class="w-7 h-7" style="color: #96EEF2" />
        Location Services
      </h1>
      <p style="color: #96EEF2">Real-time positioning and communication status</p>
    </div>

    <!-- Current Position Summary -->
    <div
      class="backdrop-blur-sm rounded-xl p-6 border mb-6"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div>
          <p class="text-sm mb-1" style="color: #96EEF2">Latitude</p>
          <p class="text-white text-lg">{{ Math.abs(currentLocation.lat).toFixed(4) }}° {{ currentLocation.lat >= 0 ? 'N' : 'S' }}</p>
        </div>
        <div>
          <p class="text-sm mb-1" style="color: #96EEF2">Longitude</p>
          <p class="text-white text-lg">{{ Math.abs(currentLocation.lon).toFixed(4) }}° {{ currentLocation.lon >= 0 ? 'E' : 'W' }}</p>
        </div>
        <div>
          <p class="text-sm mb-1" style="color: #96EEF2">Depth</p>
          <p class="text-white text-lg">{{ currentLocation.depth.toFixed(1) }} m</p>
        </div>
        <div>
          <p class="text-sm mb-1" style="color: #96EEF2">Last Update</p>
          <p class="text-white text-lg">{{ currentLocation.lastUpdate }}</p>
        </div>
      </div>
    </div>

    <!-- Iridium Section -->
    <div
      class="backdrop-blur-sm rounded-xl p-6 border mb-6"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <div class="flex items-center justify-between mb-6">
        <h2 class="text-white text-xl flex items-center gap-2">
          <Satellite class="w-6 h-6" style="color: #96EEF2" />
          Iridium Satellite Network
        </h2>
        <span class="text-sm px-3 py-1 rounded" :style="getStatusStyle(iridiumStatus)">
          {{ getStatusLabel(iridiumStatus) }}
        </span>
      </div>

      <!-- Iridium Stats -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <Signal class="w-5 h-5 mb-2" style="color: #41B9C3" />
          <p class="text-sm mb-1" style="color: #96EEF2">Signal Strength</p>
          <p class="text-white text-lg">{{ iridiumData.signalStrength }}%</p>
        </div>
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <Satellite class="w-5 h-5 mb-2" style="color: #41B9C3" />
          <p class="text-sm mb-1" style="color: #96EEF2">Satellites</p>
          <p class="text-white text-lg">{{ iridiumData.satellites }}</p>
        </div>
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <Clock class="w-5 h-5 mb-2" style="color: #41B9C3" />
          <p class="text-sm mb-1" style="color: #96EEF2">Last Transmission</p>
          <p class="text-white text-lg">{{ iridiumData.lastTransmission }}</p>
        </div>
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <Navigation class="w-5 h-5 mb-2" style="color: #41B9C3" />
          <p class="text-sm mb-1" style="color: #96EEF2">Next Scheduled</p>
          <p class="text-white text-lg">{{ iridiumData.nextScheduled }}</p>
        </div>
      </div>

      <!-- Iridium Map -->
      <div
        class="rounded-lg overflow-hidden border"
        style="border-color: rgba(65, 185, 195, 0.3); height: 400px"
      >
        <div
          class="w-full h-full relative"
          style="background: linear-gradient(135deg, rgba(14, 36, 70, 0.8) 0%, rgba(0, 77, 100, 0.6) 100%)"
        >
          <!-- Simple map visualization with grid -->
          <svg width="100%" height="100%" class="absolute inset-0">
            <!-- Grid lines -->
            <defs>
              <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
                <path d="M 40 0 L 0 0 0 40" fill="none" stroke="rgba(65, 185, 195, 0.2)" stroke-width="0.5"/>
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill="url(#grid)" />

            <!-- Coordinate lines -->
            <line x1="0" y1="50%" x2="100%" y2="50%" stroke="rgba(65, 185, 195, 0.3)" stroke-width="1" stroke-dasharray="5,5" />
            <line x1="50%" y1="0" x2="50%" y2="100%" stroke="rgba(65, 185, 195, 0.3)" stroke-width="1" stroke-dasharray="5,5" />

            <!-- Satellite positions -->
            <circle cx="30%" cy="20%" r="8" fill="#FCD869" opacity="0.8" />
            <circle cx="70%" cy="25%" r="8" fill="#FCD869" opacity="0.8" />
            <circle cx="60%" cy="70%" r="8" fill="#FCD869" opacity="0.8" />
            <circle cx="25%" cy="75%" r="8" fill="#FCD869" opacity="0.8" />

            <!-- Connection lines to center -->
            <line x1="30%" y1="20%" x2="50%" y2="50%" stroke="#41B9C3" stroke-width="1" opacity="0.5" />
            <line x1="70%" y1="25%" x2="50%" y2="50%" stroke="#41B9C3" stroke-width="1" opacity="0.5" />
            <line x1="60%" y1="70%" x2="50%" y2="50%" stroke="#41B9C3" stroke-width="1" opacity="0.5" />
            <line x1="25%" y1="75%" x2="50%" y2="50%" stroke="#41B9C3" stroke-width="1" opacity="0.5" />

            <!-- DORIS position marker -->
            <circle cx="50%" cy="50%" r="12" fill="#FF9937" />
            <circle cx="50%" cy="50%" r="6" fill="white" />
          </svg>

          <div class="absolute bottom-4 left-4 right-4">
            <div
              class="rounded-lg p-3"
              style="background-color: rgba(14, 36, 70, 0.9); border: 1px solid rgba(65, 185, 195, 0.3)"
            >
              <div class="flex items-center justify-between text-sm">
                <div class="flex items-center gap-2">
                  <div class="w-3 h-3 rounded-full" style="background-color: #FF9937"></div>
                  <span style="color: #96EEF2">DORIS Position</span>
                </div>
                <div class="flex items-center gap-2">
                  <div class="w-3 h-3 rounded-full" style="background-color: #FCD869"></div>
                  <span style="color: #96EEF2">Iridium Satellites</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- LoRa Section -->
    <div
      class="backdrop-blur-sm rounded-xl p-6 border"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <div class="flex items-center justify-between mb-6">
        <h2 class="text-white text-xl flex items-center gap-2">
          <Radio class="w-6 h-6" style="color: #96EEF2" />
          LoRa Local Network
        </h2>
        <span class="text-sm px-3 py-1 rounded" :style="getStatusStyle(loraStatus)">
          {{ getStatusLabel(loraStatus) }}
        </span>
      </div>

      <!-- LoRa Stats -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <Signal class="w-5 h-5 mb-2" style="color: #41B9C3" />
          <p class="text-sm mb-1" style="color: #96EEF2">Signal Strength</p>
          <p class="text-white text-lg">{{ loraData.signalStrength }}%</p>
        </div>
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <Navigation class="w-5 h-5 mb-2" style="color: #41B9C3" />
          <p class="text-sm mb-1" style="color: #96EEF2">Range</p>
          <p class="text-white text-lg">{{ loraData.range }}</p>
        </div>
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <Radio class="w-5 h-5 mb-2" style="color: #41B9C3" />
          <p class="text-sm mb-1" style="color: #96EEF2">Base Station</p>
          <p class="text-white text-sm">{{ loraData.baseStation }}</p>
        </div>
        <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.5)">
          <Clock class="w-5 h-5 mb-2" style="color: #41B9C3" />
          <p class="text-sm mb-1" style="color: #96EEF2">Last Ping</p>
          <p class="text-white text-lg">{{ loraData.lastPing }}</p>
        </div>
      </div>

      <!-- LoRa Map - Black and White Locator -->
      <div
        class="rounded-lg overflow-hidden border"
        style="border-color: rgba(65, 185, 195, 0.3); height: 400px"
      >
        <div class="w-full h-full relative" style="background-color: #1a1a1a">
          <!-- Black and white topographic-style map -->
          <svg width="100%" height="100%" class="absolute inset-0">
            <!-- Background gradient -->
            <defs>
              <radialGradient id="loraGradient" cx="50%" cy="50%" r="50%">
                <stop offset="0%" stop-color="#2a2a2a" />
                <stop offset="100%" stop-color="#0a0a0a" />
              </radialGradient>
            </defs>
            <rect width="100%" height="100%" fill="url(#loraGradient)" />

            <!-- Concentric circles for range -->
            <circle cx="50%" cy="50%" r="40%" fill="none" stroke="#404040" stroke-width="1" opacity="0.3" />
            <circle cx="50%" cy="50%" r="30%" fill="none" stroke="#404040" stroke-width="1" opacity="0.4" />
            <circle cx="50%" cy="50%" r="20%" fill="none" stroke="#505050" stroke-width="1" opacity="0.5" />
            <circle cx="50%" cy="50%" r="10%" fill="none" stroke="#606060" stroke-width="1" opacity="0.6" />

            <!-- Cardinal directions -->
            <line x1="50%" y1="5%" x2="50%" y2="95%" stroke="#404040" stroke-width="0.5" opacity="0.3" />
            <line x1="5%" y1="50%" x2="95%" y2="50%" stroke="#404040" stroke-width="0.5" opacity="0.3" />

            <!-- Distance markers -->
            <text x="50%" y="15%" fill="#808080" font-size="12" text-anchor="middle">N</text>
            <text x="50%" y="92%" fill="#808080" font-size="12" text-anchor="middle">S</text>
            <text x="10%" y="52%" fill="#808080" font-size="12" text-anchor="middle">W</text>
            <text x="90%" y="52%" fill="#808080" font-size="12" text-anchor="middle">E</text>

            <!-- Base station -->
            <circle cx="30%" cy="30%" r="10" fill="none" stroke="white" stroke-width="2" />
            <circle cx="30%" cy="30%" r="5" fill="white" />
            <text x="30%" y="22%" fill="white" font-size="11" text-anchor="middle">Base</text>

            <!-- DORIS position -->
            <circle cx="50%" cy="50%" r="8" fill="white" />
            <circle cx="50%" cy="50%" r="4" fill="#1a1a1a" />

            <!-- Connection line -->
            <line x1="30%" y1="30%" x2="50%" y2="50%" stroke="white" stroke-width="1.5" stroke-dasharray="3,3" opacity="0.6" />
          </svg>

          <div class="absolute bottom-4 left-4 right-4">
            <div
              class="rounded-lg p-3"
              style="background-color: rgba(0, 0, 0, 0.8); border: 1px solid #404040"
            >
              <div class="flex items-center justify-between text-sm">
                <div class="flex items-center gap-2">
                  <div class="w-3 h-3 rounded-full border-2 border-white"></div>
                  <span style="color: #c0c0c0">DORIS</span>
                </div>
                <div class="flex items-center gap-2">
                  <div class="w-3 h-3 rounded-full bg-white"></div>
                  <span style="color: #c0c0c0">Base Station</span>
                </div>
                <div class="flex items-center gap-2">
                  <span style="color: #c0c0c0">Distance: {{ loraData.range }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

