<script setup lang="ts">
import { ref } from 'vue'
import { Gauge, Wifi, WifiOff, Settings, ChevronDown, ChevronUp, Upload } from 'lucide-vue-next'
import type { SensorModule } from '../types'

const showAdvanced = ref(false)
const modules = ref<SensorModule[]>([
  { id: 1, name: 'Camera Module', type: 'camera', connected: true, power: 95, moduleStatus: 'Ready: Active' },
  { id: 3, name: 'CTD Sensor', type: 'sensor', connected: true, power: 98, sampleRate: 1, calibrationFile: 'ctd_cal_2024.cal', moduleStatus: 'Ready: Active' },
  { id: 4, name: 'Light Module', type: 'light', connected: true, power: 88, moduleStatus: 'Warning: Leak Detected' },
  { id: 6, name: 'CO/O2 Sensor', type: 'sensor', connected: false, power: 0, sampleRate: 1, moduleStatus: 'Disconnected' },
])

const selectedModule = ref<SensorModule | null>(null)

const toggleConnection = (id: number) => {
  modules.value = modules.value.map(m => 
    m.id === id ? { ...m, connected: !m.connected, power: !m.connected ? 85 : 0 } : m
  )
}

const updateSampleRate = (id: number, rate: number) => {
  modules.value = modules.value.map(m => 
    m.id === id ? { ...m, sampleRate: rate } : m
  )
}

const getModuleIcon = (type: string) => {
  switch (type) {
    case 'camera': return '📹'
    case 'sensor': return '📊'
    case 'light': return '💡'
    default: return '📦'
  }
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
      <h1 class="text-white text-2xl mb-6 flex items-center gap-2">
        <Gauge class="w-6 h-6" style="color: #96EEF2" />
        Sensor & Module Status
      </h1>

      <!-- Info Box -->
      <div 
        class="rounded-lg p-4 mb-6"
        style="background-color: rgba(65, 185, 195, 0.1); border: 1px solid rgba(65, 185, 195, 0.3)"
      >
        <p class="text-sm" style="color: #96EEF2">
          Manage all sensor and operational modules connected to DORIS. Configure data collection parameters, 
          sample rates, and module-specific settings.
        </p>
      </div>

      <!-- Module List with Inline Configuration -->
      <div class="space-y-4">
        <div v-for="module in modules" :key="module.id">
          <div
            class="p-4 transition-all cursor-pointer"
            :style="{ 
              backgroundColor: selectedModule?.id === module.id ? 'rgba(65, 185, 195, 0.2)' : 'rgba(14, 36, 70, 0.5)',
              border: selectedModule?.id === module.id ? '1px solid #41B9C3' : '1px solid rgba(65, 185, 195, 0.2)',
              borderRadius: selectedModule?.id === module.id ? '0.5rem 0.5rem 0 0' : '0.5rem',
              borderBottom: selectedModule?.id === module.id ? 'none' : '1px solid rgba(65, 185, 195, 0.2)'
            }"
            @click="selectedModule = selectedModule?.id === module.id ? null : module"
          >
            <div class="flex items-center justify-between mb-3">
              <div class="flex items-center gap-3">
                <span class="text-2xl">{{ getModuleIcon(module.type) }}</span>
                <div>
                  <h3 class="text-white">{{ module.name }}</h3>
                  <p class="text-sm capitalize" style="color: #96EEF2">{{ module.type }}</p>
                </div>
              </div>
              <button
                @click.stop="toggleConnection(module.id)"
                class="p-2 rounded-lg transition-all"
                :style="{ 
                  backgroundColor: module.connected ? 'rgba(252, 216, 105, 0.2)' : 'rgba(14, 36, 70, 0.5)',
                  color: module.connected ? '#FCD869' : '#96EEF2'
                }"
              >
                <Wifi v-if="module.connected" class="w-5 h-5" />
                <WifiOff v-else class="w-5 h-5" />
              </button>
            </div>

            <div class="space-y-2">
              <div class="flex items-center justify-between text-sm">
                <span style="color: #96EEF2">Status</span>
                <span :style="{ color: getStatusColor(module.moduleStatus) }">
                  {{ module.moduleStatus }}
                </span>
              </div>
              <div class="flex items-center justify-between text-sm">
                <span style="color: #96EEF2">Connection Status</span>
                <span :style="{ color: module.connected ? '#FCD869' : '#DD2C1D' }">
                  {{ module.connected ? 'Connected' : 'Disconnected' }}
                </span>
              </div>
              <div class="flex items-center justify-between text-sm">
                <span style="color: #96EEF2">Power Usage</span>
                <span class="text-white">{{ module.power }}%</span>
              </div>
              <div 
                v-if="module.type === 'sensor' && module.sampleRate !== undefined"
                class="flex items-center justify-between text-sm"
              >
                <span style="color: #96EEF2">Sample Rate</span>
                <span class="text-white">{{ module.sampleRate }} Hz</span>
              </div>
            </div>
          </div>

          <!-- Expanded Configuration for Selected Module -->
          <div 
            v-if="selectedModule?.id === module.id"
            class="p-6 animate-slide-in"
            style="background-color: rgba(65, 185, 195, 0.15); border: 1px solid #41B9C3; border-top: 1px solid rgba(65, 185, 195, 0.3); border-radius: 0 0 0.5rem 0.5rem"
          >
            <h2 class="text-white mb-4 flex items-center gap-2">
              <Settings class="w-5 h-5" style="color: #96EEF2" />
              {{ selectedModule.name }} Configuration
            </h2>

            <div v-if="selectedModule.type === 'sensor'" class="space-y-6">
              <!-- Basic Sensor Settings -->
              <div>
                <h3 class="mb-3" style="color: #96EEF2">Basic Settings</h3>
                <div class="space-y-4">
                  <div>
                    <label class="block text-sm mb-2" style="color: #96EEF2">Recording Mode</label>
                    <select 
                      class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                      style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
                    >
                      <option>Continuous Recording</option>
                      <option>Interval Recording</option>
                      <option>Event Triggered</option>
                    </select>
                  </div>
                  <div>
                    <label class="block text-sm mb-2" style="color: #96EEF2">Sample Rate (Hz)</label>
                    <select 
                      :value="selectedModule.sampleRate"
                      @change="updateSampleRate(selectedModule.id, parseFloat(($event.target as HTMLSelectElement).value))"
                      class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                      style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
                    >
                      <option value="0.1">0.1 Hz (every 10 seconds)</option>
                      <option value="0.5">0.5 Hz (every 2 seconds)</option>
                      <option value="1">1 Hz (every second)</option>
                      <option value="5">5 Hz</option>
                      <option value="10">10 Hz</option>
                    </select>
                  </div>
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
              </div>

              <!-- Advanced Sensor Settings -->
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

                <div v-if="showAdvanced" class="space-y-4">
                  <div>
                    <label class="block text-sm mb-2" style="color: #96EEF2">Data Format</label>
                    <select 
                      class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                      style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
                    >
                      <option>CSV</option>
                      <option>JSON</option>
                      <option>Binary</option>
                    </select>
                  </div>
                  <div>
                    <label class="block text-sm mb-2" style="color: #96EEF2">Trigger Conditions</label>
                    <select 
                      class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                      style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
                    >
                      <option>Always On</option>
                      <option>Depth Threshold</option>
                      <option>Temperature Change</option>
                      <option>Time-based</option>
                    </select>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="selectedModule.type === 'camera'" class="space-y-4">
              <p style="color: #96EEF2">Camera-specific settings are available in the Mission Programming section.</p>
              <button 
                class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90"
                style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
              >
                Go to Mission Programming
              </button>
            </div>

            <div v-if="selectedModule.type === 'light'" class="space-y-4">
              <div>
                <label class="block text-sm mb-2" style="color: #96EEF2">Brightness Level (%)</label>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value="75"
                  class="w-full"
                />
              </div>
              <div>
                <label class="block text-sm mb-2" style="color: #96EEF2">Light Mode</label>
                <select 
                  class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                  style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
                >
                  <option>Continuous</option>
                  <option>Pulsed</option>
                  <option>Synchronized with Camera</option>
                </select>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

