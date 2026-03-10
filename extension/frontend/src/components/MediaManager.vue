<script setup lang="ts">
import { ref, computed } from 'vue'
import { Download, FolderOpen, Image as ImageIcon, Video, FileText, Calendar, Search, Package, Cloud } from 'lucide-vue-next'
import type { MediaFile, MissionData } from '../types'

const viewMode = ref<'missions' | 'files'>('missions')
const selectedMission = ref<MissionData | null>(null)
const searchQuery = ref('')
const filterType = ref<'all' | 'video' | 'image' | 'sensor'>('all')
const showSensorData = ref(false)

const missions = ref<MissionData[]>([
  { id: 1, name: 'Mission II', date: '2024-11-20', files: 45, totalSize: '12.5 GB', gpsPosition: '41.7128° N, 74.0060° W', duration: '2h 15m' },
  { id: 2, name: 'Deep_Reef_Survey', date: '2024-11-18', files: 78, totalSize: '28.3 GB', gpsPosition: '42.3601° N, 71.0589° W', duration: '3h 45m' },
  { id: 3, name: 'Coastal_Study_01', date: '2024-11-15', files: 32, totalSize: '8.7 GB', gpsPosition: '40.7580° N, 73.9855° W', duration: '1h 30m' },
])

const mediaFiles = ref<MediaFile[]>([
  { id: 1, name: 'VID_20241120_143022_Mission_II.mp4', type: 'video', mission: 'Mission II', date: '2024-11-20', size: '2.3 GB', timestamp: '14:30:22 UTC' },
  { id: 2, name: 'IMG_20241120_143530_Mission_II.TIFF', type: 'image', mission: 'Mission II', date: '2024-11-20', size: '45 MB', timestamp: '14:35:30 UTC' },
  { id: 3, name: 'CTD_20241120_143000_Mission_II.csv', type: 'sensor', mission: 'Mission II', date: '2024-11-20', size: '1.2 MB', timestamp: '14:30:00 UTC' },
  { id: 4, name: 'VID_20241120_150045_Mission_II.mp4', type: 'video', mission: 'Mission II', date: '2024-11-20', size: '3.1 GB', timestamp: '15:00:45 UTC' },
])

const filteredFiles = computed(() => {
  return mediaFiles.value.filter(file => {
    const matchesSearch = file.name.toLowerCase().includes(searchQuery.value.toLowerCase())
    const matchesFilter = filterType.value === 'all' || file.type === filterType.value
    return matchesSearch && matchesFilter
  })
})

const getFileIconColor = (type: string) => {
  switch (type) {
    case 'video': return '#a855f7'
    case 'image': return '#3b82f6'
    case 'sensor': return '#22c55e'
    default: return '#06b6d4'
  }
}

const handleDownloadMission = (mission: MissionData) => {
  alert(`Preparing download for ${mission.name}...\nThis will create: ${mission.date}_${mission.name}.zip`)
}

const handleSyncToCloud = () => {
  alert('Starting cloud sync... All mission data will be uploaded.')
}
</script>

<template>
  <div class="max-w-7xl mx-auto px-4 py-6 md:py-8">
    <div 
      class="backdrop-blur-sm rounded-xl p-6 border"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
        <h1 class="text-white text-2xl flex items-center gap-2">
          <FolderOpen class="w-6 h-6" style="color: #96EEF2" />
          Media Manager & Data Viewer
        </h1>
        <div class="flex gap-2">
          <button
            @click="handleSyncToCloud"
            class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 flex items-center gap-2"
            style="background: linear-gradient(135deg, #FF9937 0%, #FF621D 100%)"
          >
            <Cloud class="w-4 h-4" />
            Sync to Cloud
          </button>
        </div>
      </div>

      <!-- Storage Info -->
      <div 
        class="rounded-lg p-4 mb-6 flex items-center justify-between"
        style="background-color: rgba(65, 185, 195, 0.1); border: 1px solid rgba(65, 185, 195, 0.3)"
      >
        <div>
          <p style="color: #96EEF2">Total Storage: 500 GB</p>
          <p style="color: #41B9C3">Available: 275 GB (55%)</p>
        </div>
        <div class="text-right">
          <p style="color: #96EEF2">Total Missions: {{ missions.length }}</p>
          <p style="color: #41B9C3">Total Files: {{ mediaFiles.length }}</p>
        </div>
      </div>

      <!-- View Mode Toggle -->
      <div class="flex gap-2 mb-6">
        <button
          @click="viewMode = 'missions'"
          class="px-4 py-2 rounded-lg transition-all"
          :style="viewMode === 'missions' 
            ? { background: 'linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)', color: 'white' }
            : { backgroundColor: 'rgba(14, 36, 70, 0.5)', color: '#96EEF2', border: '1px solid rgba(65, 185, 195, 0.3)' }"
        >
          View by Mission
        </button>
        <button
          @click="viewMode = 'files'"
          class="px-4 py-2 rounded-lg transition-all"
          :style="viewMode === 'files' 
            ? { background: 'linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)', color: 'white' }
            : { backgroundColor: 'rgba(14, 36, 70, 0.5)', color: '#96EEF2', border: '1px solid rgba(65, 185, 195, 0.3)' }"
        >
          View All Files
        </button>
      </div>

      <!-- Search and Filter -->
      <div class="flex flex-col md:flex-row gap-3 mb-6">
        <div class="flex-1 relative">
          <Search class="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5" style="color: #41B9C3" />
          <input
            type="text"
            v-model="searchQuery"
            placeholder="Search by mission name or keyword..."
            class="w-full pl-10 pr-4 py-2 text-white rounded-lg focus:outline-none"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
          />
        </div>
        <select
          v-model="filterType"
          class="px-4 py-2 text-white rounded-lg focus:outline-none"
          style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
        >
          <option value="all">All Types</option>
          <option value="video">Videos</option>
          <option value="image">Images</option>
          <option value="sensor">Sensor Data</option>
        </select>
      </div>

      <!-- Missions View -->
      <div v-if="viewMode === 'missions'" class="space-y-4">
        <div
          v-for="mission in missions"
          :key="mission.id"
          class="rounded-lg p-5 transition-all"
          style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)"
        >
          <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div class="flex-1">
              <div class="flex items-center gap-3 mb-2">
                <h3 class="text-white text-lg">{{ mission.name }}</h3>
                <span class="text-sm flex items-center gap-1" style="color: #41B9C3">
                  <Calendar class="w-4 h-4" />
                  {{ mission.date }}
                </span>
              </div>
              <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                <div>
                  <p style="color: #96EEF2">Files</p>
                  <p class="text-white">{{ mission.files }}</p>
                </div>
                <div>
                  <p style="color: #96EEF2">Size</p>
                  <p class="text-white">{{ mission.totalSize }}</p>
                </div>
                <div>
                  <p style="color: #96EEF2">Duration</p>
                  <p class="text-white">{{ mission.duration }}</p>
                </div>
                <div>
                  <p style="color: #96EEF2">GPS Position</p>
                  <p class="text-white">{{ mission.gpsPosition }}</p>
                </div>
              </div>
            </div>
            <div class="flex flex-col sm:flex-row gap-2 w-full sm:w-auto">
              <button
                @click="selectedMission = mission; showSensorData = true"
                class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 flex items-center justify-center gap-2"
                style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
              >
                <FileText class="w-4 h-4" />
                <span>View Data</span>
              </button>
              <button
                @click="handleDownloadMission(mission)"
                class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 flex items-center justify-center gap-2"
                style="background: linear-gradient(135deg, #FF9937 0%, #FF621D 100%)"
              >
                <Package class="w-4 h-4" />
                <span>Download All</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Files View -->
      <div v-if="viewMode === 'files'" class="space-y-2">
        <div
          v-for="file in filteredFiles"
          :key="file.id"
          class="rounded-lg p-4 flex items-center justify-between transition-all"
          style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)"
        >
          <div class="flex items-center gap-3 flex-1">
            <Video v-if="file.type === 'video'" class="w-5 h-5" :style="{ color: getFileIconColor(file.type) }" />
            <ImageIcon v-else-if="file.type === 'image'" class="w-5 h-5" :style="{ color: getFileIconColor(file.type) }" />
            <FileText v-else class="w-5 h-5" :style="{ color: getFileIconColor(file.type) }" />
            <div class="flex-1">
              <p class="text-white">{{ file.name }}</p>
              <div class="flex items-center gap-4 text-sm mt-1" style="color: #96EEF2">
                <span>{{ file.mission }}</span>
                <span>{{ file.timestamp }}</span>
                <span>{{ file.size }}</span>
              </div>
            </div>
          </div>
          <button 
            class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 flex items-center gap-2"
            style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
          >
            <Download class="w-4 h-4" />
            Download
          </button>
        </div>
      </div>

      <!-- Sensor Data Visualization -->
      <div 
        v-if="showSensorData && selectedMission"
        class="mt-6 rounded-lg p-6"
        style="background-color: rgba(14, 36, 70, 0.3); border: 1px solid rgba(65, 185, 195, 0.2)"
      >
        <div class="flex items-center justify-between mb-6">
          <h2 class="text-white text-xl">Sensor Data - {{ selectedMission.name }}</h2>
          <button
            @click="showSensorData = false"
            class="transition-colors"
            style="color: #41B9C3"
          >
            Close
          </button>
        </div>

        <!-- Placeholder for charts -->
        <div class="space-y-6">
          <div 
            class="h-48 rounded-lg flex items-center justify-center"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)"
          >
            <div class="text-center">
              <p style="color: #96EEF2">Depth Profile Chart</p>
              <p class="text-sm" style="color: #41B9C3">(Chart visualization placeholder)</p>
            </div>
          </div>
          <div 
            class="h-48 rounded-lg flex items-center justify-center"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)"
          >
            <div class="text-center">
              <p style="color: #96EEF2">Temperature Profile Chart</p>
              <p class="text-sm" style="color: #41B9C3">(Chart visualization placeholder)</p>
            </div>
          </div>
        </div>

        <!-- Export Options -->
        <div class="flex gap-2 mt-6">
          <button 
            class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 flex items-center gap-2"
            style="background: linear-gradient(135deg, #FCD869 0%, #FF9937 100%)"
          >
            <Download class="w-4 h-4" />
            Export as CSV
          </button>
          <button 
            class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 flex items-center gap-2"
            style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
          >
            <Package class="w-4 h-4" />
            Download Mission Package
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

