<script setup lang="ts">
import { ref, computed } from 'vue'
import { Calendar, Clock, Settings, Save, Copy, AlertTriangle, ChevronDown, ChevronUp, Database, MapPin, Camera, Image, Battery, Zap } from 'lucide-vue-next'
import type { CameraSettings, Duration, Timelapse, Mission } from '../types'

const missionName = ref('Mission II')
const showAdvanced = ref(false)
const showBatteryPlanning = ref(false)
const startTrigger = ref<'time' | 'manual' | 'depth'>('manual')
const endTrigger = ref<'time' | 'duration' | 'depth'>('duration')
const timelapse = ref<Timelapse>({ enabled: true, interval: 60 })
const duration = ref<Duration>({ value: 60, unit: 'seconds' })
const lightingBrightness = ref(75)
const cameraSettings = ref<CameraSettings>({
  resolution: '4K',
  frameRate: 30,
  focus: 'auto'
})
const warnings = ref<string[]>([])

const calculateBatteryUsage = computed(() => {
  let durationInHours = duration.value.value
  if (duration.value.unit === 'seconds') durationInHours = duration.value.value / 3600
  if (duration.value.unit === 'minutes') durationInHours = duration.value.value / 60

  const basePower = 2
  let cameraPower = 3
  if (cameraSettings.value.resolution === '4K') cameraPower = 5
  if (cameraSettings.value.resolution === '2.7K') cameraPower = 4
  if (cameraSettings.value.frameRate === 60) cameraPower *= 1.3

  const lightingPower = (lightingBrightness.value / 100) * 15
  const timelapsePower = timelapse.value.enabled ? 0.5 : 0
  const sensorPower = 1.5
  const totalPower = basePower + cameraPower + lightingPower + timelapsePower + sensorPower
  const batteryCapacity = 100
  const batteryLife = batteryCapacity / totalPower
  const batteryUsagePercent = Math.min((durationInHours / batteryLife) * 100, 100)

  return {
    basePower,
    cameraPower,
    lightingPower,
    timelapsePower,
    sensorPower,
    totalPower,
    batteryLife,
    batteryUsagePercent,
    durationInHours
  }
})

const previousMissions = ref<Mission[]>([
  { id: 1, name: 'Deep Sea Survey 2024-01', date: 'Jan 5, 2026', duration: '3h 45m', location: '41.7128° N, 74.0060° W', maxDepth: 125, images: 487, videos: 3, status: 'completed' },
  { id: 2, name: 'Coral Reef Documentation', date: 'Jan 2, 2026', duration: '2h 18m', location: '25.7617° N, 80.1918° W', maxDepth: 45, images: 324, videos: 5, status: 'completed' },
  { id: 3, name: 'Kelp Forest Study', date: 'Dec 28, 2025', duration: '4h 12m', location: '36.6002° N, 121.8947° W', maxDepth: 78, images: 612, videos: 7, status: 'completed' },
  { id: 4, name: 'Shipwreck Investigation', date: 'Dec 20, 2025', duration: '5h 30m', location: '42.3601° N, 71.0589° W', maxDepth: 156, images: 893, videos: 12, status: 'completed' },
  { id: 5, name: 'Bioluminescence Survey', date: 'Dec 15, 2025', duration: '6h 05m', location: '32.7157° N, 117.1611° W', maxDepth: 98, images: 1247, videos: 8, status: 'completed' }
])

const handleSaveMission = () => {
  const newWarnings: string[] = []
  if (duration.value.value < 30 && duration.value.unit === 'seconds') {
    newWarnings.push('Mission duration is very short. Consider extending the duration.')
  }
  if (lightingBrightness.value < 50) {
    newWarnings.push('Low lighting brightness may affect video quality in deep water.')
  }
  warnings.value = newWarnings
}

const getTriggerStyle = (isActive: boolean) => {
  if (isActive) {
    return { background: 'linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)', border: '1px solid #41B9C3', color: 'white' }
  }
  return { backgroundColor: 'rgba(14, 36, 70, 0.5)', border: '1px solid rgba(65, 185, 195, 0.3)', color: '#96EEF2' }
}
</script>

<template>
  <div class="max-w-6xl mx-auto px-4 py-6 md:py-8">
    <div 
      class="backdrop-blur-sm rounded-xl p-6 border"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <div class="flex items-center justify-between mb-6">
        <h1 class="text-white text-2xl flex items-center gap-2">
          <Settings class="w-6 h-6" style="color: #96EEF2" />
          Mission Programming
        </h1>
        <div class="flex gap-2 flex-shrink-0">
          <button 
            class="px-4 py-2 rounded-lg transition-all flex items-center gap-2"
            style="background-color: rgba(14, 36, 70, 0.5); color: #96EEF2; border: 1px solid rgba(65, 185, 195, 0.3)"
          >
            <Copy class="w-4 h-4" />
            <span class="hidden sm:inline">Copy</span>
          </button>
          <button 
            @click="handleSaveMission"
            class="px-4 py-2 text-white rounded-lg transition-all flex items-center gap-2 hover:opacity-90"
            style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
          >
            <Save class="w-4 h-4" />
            <span class="hidden sm:inline">Save Mission</span>
          </button>
        </div>
      </div>

      <!-- Warnings -->
      <div 
        v-if="warnings.length > 0"
        class="rounded-lg p-4 mb-6"
        style="background-color: rgba(255, 153, 55, 0.1); border: 1px solid rgba(255, 153, 55, 0.3)"
      >
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

      <!-- Mission Name -->
      <div class="mb-6">
        <label class="block mb-2" style="color: #96EEF2">Mission Name</label>
        <input
          type="text"
          v-model="missionName"
          class="w-full px-4 py-3 text-white rounded-lg focus:outline-none"
          style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
        />
      </div>

      <!-- Start Trigger -->
      <div class="mb-6">
        <label class="block mb-3" style="color: #96EEF2">Start Trigger</label>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <button
            @click="startTrigger = 'manual'"
            class="p-4 rounded-lg transition-all"
            :style="getTriggerStyle(startTrigger === 'manual')"
          >
            Manual Start
          </button>
          <button
            @click="startTrigger = 'time'"
            class="p-4 rounded-lg transition-all"
            :style="getTriggerStyle(startTrigger === 'time')"
          >
            <Calendar class="w-5 h-5 mx-auto mb-1" />
            By Time/Date
          </button>
          <button
            @click="startTrigger = 'depth'"
            class="p-4 rounded-lg transition-all"
            :style="getTriggerStyle(startTrigger === 'depth')"
          >
            By Depth
          </button>
        </div>
      </div>

      <!-- Timelapse Photo -->
      <div class="mb-6">
        <div class="flex items-center justify-between mb-3">
          <label style="color: #96EEF2">Timelapse Photo</label>
          <label class="toggle-switch">
            <input type="checkbox" v-model="timelapse.enabled" />
            <span class="toggle-slider"></span>
          </label>
        </div>

        <div 
          v-if="timelapse.enabled"
          class="p-4 rounded-lg"
          style="background-color: rgba(14, 36, 70, 0.3)"
        >
          <label class="block text-sm mb-2" style="color: #96EEF2">Capture every (seconds)</label>
          <input
            type="number"
            v-model="timelapse.interval"
            class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
          />
        </div>
      </div>

      <!-- End Trigger -->
      <div class="mb-6">
        <label class="block mb-3" style="color: #96EEF2">End Trigger</label>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
          <button
            @click="endTrigger = 'duration'"
            class="p-4 rounded-lg transition-all"
            :style="getTriggerStyle(endTrigger === 'duration')"
          >
            <Clock class="w-5 h-5 mx-auto mb-1" />
            After Duration
          </button>
          <button
            @click="endTrigger = 'time'"
            class="p-4 rounded-lg transition-all"
            :style="getTriggerStyle(endTrigger === 'time')"
          >
            By Time/Date
          </button>
          <button
            @click="endTrigger = 'depth'"
            class="p-4 rounded-lg transition-all"
            :style="getTriggerStyle(endTrigger === 'depth')"
          >
            By Depth
          </button>
        </div>

        <div 
          v-if="endTrigger === 'duration'"
          class="p-4 rounded-lg"
          style="background-color: rgba(14, 36, 70, 0.3)"
        >
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="block text-sm mb-2" style="color: #96EEF2">Duration</label>
              <input
                type="number"
                v-model="duration.value"
                class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
              />
            </div>
            <div>
              <label class="block text-sm mb-2" style="color: #96EEF2">Unit</label>
              <select
                v-model="duration.unit"
                class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
              >
                <option value="seconds">Seconds</option>
                <option value="minutes">Minutes</option>
                <option value="hours">Hours</option>
              </select>
            </div>
          </div>
        </div>
      </div>

      <!-- Basic Camera & Lighting -->
      <div class="mb-6">
        <h2 class="text-white mb-4">Camera & Lighting</h2>
        
        <!-- Lighting Brightness -->
        <div class="mb-4">
          <label class="block text-sm mb-2" style="color: #96EEF2">
            Lighting Brightness: {{ lightingBrightness }}%
          </label>
          <input
            type="range"
            min="50"
            max="100"
            v-model="lightingBrightness"
            class="w-full"
          />
          <p class="text-sm mt-2" style="color: #41B9C3">
            Camera turns on 1 second before lights activate
          </p>
        </div>

        <!-- Basic Camera Settings -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label class="block text-sm mb-2" style="color: #96EEF2">Resolution</label>
            <select
              v-model="cameraSettings.resolution"
              class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
              style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
            >
              <option value="1080p">1080p</option>
              <option value="4K">4K</option>
              <option value="2.7K">2.7K</option>
            </select>
          </div>
          <div>
            <label class="block text-sm mb-2" style="color: #96EEF2">Frame Rate</label>
            <select
              v-model.number="cameraSettings.frameRate"
              class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
              style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
            >
              <option :value="24">24 fps</option>
              <option :value="30">30 fps</option>
              <option :value="60">60 fps</option>
            </select>
          </div>
          <div>
            <label class="block text-sm mb-2" style="color: #96EEF2">Focus</label>
            <select
              v-model="cameraSettings.focus"
              class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
              style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
            >
              <option value="auto">Auto Focus</option>
              <option value="fixed">Fixed Focus</option>
            </select>
          </div>
        </div>
      </div>

      <!-- Advanced Settings -->
      <div class="pt-6" style="border-top: 1px solid rgba(65, 185, 195, 0.2)">
        <button
          @click="showAdvanced = !showAdvanced"
          class="flex items-center gap-2 transition-colors mb-4"
          style="color: #41B9C3"
        >
          <ChevronUp v-if="showAdvanced" class="w-5 h-5" />
          <ChevronDown v-else class="w-5 h-5" />
          {{ showAdvanced ? 'Hide' : 'Show' }} Advanced Settings
        </button>

        <div v-if="showAdvanced" class="space-y-6">
          <!-- Advanced Camera Settings -->
          <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.3)">
            <h3 class="text-white mb-4">Advanced Camera Settings</h3>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label class="block text-sm mb-2" style="color: #96EEF2">ISO</label>
                <select 
                  class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                  style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
                >
                  <option>Auto</option>
                  <option>100</option>
                  <option>200</option>
                  <option>400</option>
                  <option>800</option>
                </select>
              </div>
              <div>
                <label class="block text-sm mb-2" style="color: #96EEF2">White Balance</label>
                <select 
                  class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                  style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
                >
                  <option>Auto</option>
                  <option>3000K</option>
                  <option>5500K</option>
                  <option>6500K</option>
                </select>
              </div>
            </div>
          </div>

          <!-- Automated Actions -->
          <div class="rounded-lg p-4" style="background-color: rgba(14, 36, 70, 0.3)">
            <h3 class="text-white mb-4">Automated Actions</h3>
            <div class="space-y-3">
              <div 
                class="flex items-center justify-between p-3 rounded-lg"
                style="background-color: rgba(14, 36, 70, 0.5)"
              >
                <span style="color: #96EEF2">Enable sleep mode when idle</span>
                <label class="toggle-switch">
                  <input type="checkbox" />
                  <span class="toggle-slider"></span>
                </label>
              </div>
              <div 
                class="flex items-center justify-between p-3 rounded-lg"
                style="background-color: rgba(14, 36, 70, 0.5)"
              >
                <span style="color: #96EEF2">Detect bottom rest vs water movement</span>
                <label class="toggle-switch">
                  <input type="checkbox" checked />
                  <span class="toggle-slider"></span>
                </label>
              </div>
              <div 
                class="flex items-center justify-between p-3 rounded-lg"
                style="background-color: rgba(14, 36, 70, 0.5)"
              >
                <span style="color: #96EEF2">
                  Surface arrival notifications 
                  <span style="color: rgba(150, 238, 242, 0.6); font-size: 0.875rem">(always on)</span>
                </span>
                <label class="toggle-switch cursor-not-allowed opacity-75">
                  <input type="checkbox" checked disabled />
                  <span class="toggle-slider" style="background-color: #41B9C3"></span>
                </label>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Battery Planning -->
      <div class="pt-6" style="border-top: 1px solid rgba(65, 185, 195, 0.2)">
        <button
          @click="showBatteryPlanning = !showBatteryPlanning"
          class="flex items-center gap-2 transition-colors mb-4"
          style="color: #41B9C3"
        >
          <ChevronUp v-if="showBatteryPlanning" class="w-5 h-5" />
          <ChevronDown v-else class="w-5 h-5" />
          {{ showBatteryPlanning ? 'Hide' : 'Show' }} Battery Planning
        </button>

        <div v-if="showBatteryPlanning" class="space-y-6">
          <!-- Battery Overview -->
          <div class="rounded-lg p-6" style="background-color: rgba(14, 36, 70, 0.3)">
            <div class="flex items-center gap-3 mb-6">
              <Battery class="w-6 h-6" style="color: #96EEF2" />
              <h3 class="text-white text-lg">Battery Overview</h3>
            </div>
            
            <!-- Battery Visual Bar -->
            <div class="mb-6">
              <div class="flex items-center justify-between mb-2">
                <span style="color: #96EEF2">Estimated Usage for Mission</span>
                <span 
                  class="text-xl font-semibold"
                  :style="{ color: calculateBatteryUsage.batteryUsagePercent > 80 ? '#DD2C1D' : '#41B9C3' }"
                >
                  {{ calculateBatteryUsage.batteryUsagePercent.toFixed(1) }}%
                </span>
              </div>
              <div 
                class="w-full h-8 rounded-lg overflow-hidden"
                style="background-color: rgba(14, 36, 70, 0.5)"
              >
                <div 
                  class="h-full transition-all duration-300"
                  :style="{ 
                    width: `${calculateBatteryUsage.batteryUsagePercent}%`,
                    background: calculateBatteryUsage.batteryUsagePercent > 80 
                      ? 'linear-gradient(90deg, #DD2C1D 0%, #FF6B6B 100%)'
                      : 'linear-gradient(90deg, #41B9C3 0%, #96EEF2 100%)'
                  }"
                ></div>
              </div>
              <div class="flex items-center justify-between mt-2 text-sm" style="color: #96EEF2">
                <span>Mission Duration: {{ calculateBatteryUsage.durationInHours.toFixed(2) }}h</span>
                <span>Total Battery Life: {{ calculateBatteryUsage.batteryLife.toFixed(2) }}h</span>
              </div>
            </div>

            <!-- Key Stats -->
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
              <div 
                class="rounded-lg p-4"
                style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
              >
                <div class="flex items-center gap-2 mb-2">
                  <Zap class="w-4 h-4" style="color: #96EEF2" />
                  <span class="text-sm" style="color: #96EEF2">Total Power Draw</span>
                </div>
                <p class="text-2xl font-semibold text-white">{{ calculateBatteryUsage.totalPower.toFixed(1) }}W</p>
              </div>
              <div 
                class="rounded-lg p-4"
                style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
              >
                <div class="flex items-center gap-2 mb-2">
                  <Battery class="w-4 h-4" style="color: #96EEF2" />
                  <span class="text-sm" style="color: #96EEF2">Battery Capacity</span>
                </div>
                <p class="text-2xl font-semibold text-white">100Wh</p>
              </div>
              <div 
                class="rounded-lg p-4"
                style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
              >
                <div class="flex items-center gap-2 mb-2">
                  <Clock class="w-4 h-4" style="color: #96EEF2" />
                  <span class="text-sm" style="color: #96EEF2">Max Runtime</span>
                </div>
                <p class="text-2xl font-semibold text-white">{{ calculateBatteryUsage.batteryLife.toFixed(1) }}h</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Previous Missions -->
    <div 
      class="backdrop-blur-sm rounded-xl p-6 border mt-6"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <h2 class="text-white text-xl mb-6 flex items-center gap-2">
        <Database class="w-5 h-5" style="color: #96EEF2" />
        Previous Missions
      </h2>
      <div class="space-y-3">
        <div
          v-for="mission in previousMissions"
          :key="mission.id"
          class="rounded-lg p-5 transition-all hover:bg-slate-700/50"
          style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)"
        >
          <div class="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
            <div class="flex-1">
              <div class="flex items-center gap-3 mb-3">
                <h3 class="text-white text-lg">{{ mission.name }}</h3>
                <span 
                  class="px-2 py-1 rounded text-xs"
                  style="background-color: rgba(252, 216, 105, 0.2); color: #FCD869"
                >
                  {{ mission.status }}
                </span>
              </div>
              
              <div class="grid grid-cols-1 md:grid-cols-4 gap-3 mb-3">
                <div class="flex items-center gap-2">
                  <Calendar class="w-4 h-4" style="color: #96EEF2" />
                  <span class="text-sm" style="color: #96EEF2">{{ mission.date }}</span>
                </div>
                <div class="flex items-center gap-2">
                  <Clock class="w-4 h-4" style="color: #96EEF2" />
                  <span class="text-sm" style="color: #96EEF2">{{ mission.duration }}</span>
                </div>
                <div class="flex items-center gap-2">
                  <MapPin class="w-4 h-4" style="color: #96EEF2" />
                  <span class="text-sm" style="color: #96EEF2">{{ mission.location }}</span>
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-sm" style="color: #96EEF2">Max Depth: {{ mission.maxDepth }}m</span>
                </div>
              </div>

              <div class="flex items-center gap-4">
                <div class="flex items-center gap-2">
                  <Camera class="w-4 h-4" style="color: #41B9C3" />
                  <span class="text-sm" style="color: #96EEF2">{{ mission.images }} images</span>
                </div>
                <div class="flex items-center gap-2">
                  <Image class="w-4 h-4" style="color: #41B9C3" />
                  <span class="text-sm" style="color: #96EEF2">{{ mission.videos }} videos</span>
                </div>
              </div>
            </div>

            <button
              class="flex-shrink-0 p-3 rounded-lg transition-all hover:opacity-80 self-start md:self-auto"
              style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%); color: white"
              title="View Mission Media"
            >
              <Database class="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

