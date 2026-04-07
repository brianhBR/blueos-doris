<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  Database, Calendar, Clock, MapPin, Camera, Image, FileText, Play,
  Download, Trash2, AlertTriangle, Archive, Search, X
} from 'lucide-vue-next'
import { useDiveHistory } from '../composables/useApi'
import { parseBackendDateTime } from '../parseBackendTime'
import type { DiveHistorySummary } from '../composables/useApi'
import type { Screen, DiveData } from '../types'

const emit = defineEmits<{
  navigate: [screen: Screen, diveData?: DiveData]
}>()

const { dives: apiDives, loading, error, fetchDives, deleteDiveRecord } = useDiveHistory()

function mcapDownloadHref(relativePath: string): string {
  return `/api/v1/media/download?path=${encodeURIComponent(relativePath)}`
}

function scientificCsvHref(diveId: string): string {
  return `/api/v1/dive/history/${encodeURIComponent(diveId)}/export/scientific.csv`
}

function formatStatusLabel(status: string): string {
  const s = status.toLowerCase()
  if (s === 'active') return 'Active'
  if (s === 'completed') return 'Completed'
  if (s === 'cancelled') return 'Cancelled'
  return status
}

interface DisplayMission {
  id: string
  name: string
  configuration: string
  status: string
  statusLabel: string
  dateDisplay: string
  /** 24-hour hh:mm UTC */
  timeDisplay: string
  dateMs: number
  duration: string
  location: string
  maxDepthLabel: string
  maxDepthFromLog: boolean
  mcapRelativePath: string | null
  images: number
  videos: number
}

const previousMissions = computed<DisplayMission[]>(() => {
  return apiDives.value.map((m: DiveHistorySummary) => {
    const dt = parseBackendDateTime(String(m.date))
    const dateMs = Number.isNaN(dt.getTime()) ? 0 : dt.getTime()
    const md =
      m.max_depth != null && Number.isFinite(m.max_depth) && m.max_depth > 0
        ? `${m.max_depth}m`
        : '—'
    const logMd = m.log_max_depth_m
    const maxDepthFromLog =
      logMd != null && Number.isFinite(logMd) && logMd > 0
    return {
      id: m.id,
      name: m.name,
      configuration: (m.configuration ?? '').trim(),
      status: m.status,
      statusLabel: formatStatusLabel(m.status),
      dateDisplay: dt.toLocaleDateString(undefined, {
        timeZone: 'UTC',
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      }),
      timeDisplay:
        dt.toLocaleTimeString(undefined, {
          timeZone: 'UTC',
          hour: '2-digit',
          minute: '2-digit',
          hour12: false,
        }) + ' UTC',
      dateMs,
      duration: m.duration,
      location: m.location?.trim() ? m.location : '—',
      maxDepthLabel: md,
      maxDepthFromLog,
      mcapRelativePath: m.mcap_relative_path ?? null,
      images: m.image_count,
      videos: m.video_count,
    }
  })
})

let pollInterval: number | undefined

onMounted(() => {
  fetchDives()
  pollInterval = setInterval(fetchDives, 15000) as unknown as number
})

onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval)
})

const selectedMission = ref<string | null>(null)
const nameFilter = ref('')
const dateSort = ref<'recent' | 'oldest'>('recent')
const isDeleting = ref(false)

const filteredMissions = computed(() => {
  return previousMissions.value
    .filter(mission => mission.name.toLowerCase().includes(nameFilter.value.toLowerCase()))
    .sort((a, b) => {
      return dateSort.value === 'recent' ? b.dateMs - a.dateMs : a.dateMs - b.dateMs
    })
})

const handleViewMedia = (mission: DisplayMission) => {
  const diveData: DiveData = {
    name: mission.name,
    date: `${mission.dateDisplay} · ${mission.timeDisplay}`,
    duration: mission.duration,
    maxDepth: mission.maxDepthLabel,
    location: mission.location,
    images: mission.images,
    videos: mission.videos
  }
  emit('navigate', 'viewmedia', diveData)
}

const handleDeleteMission = async () => {
  if (!selectedMission.value) return
  isDeleting.value = true
  const ok = await deleteDiveRecord(selectedMission.value)
  if (ok) selectedMission.value = null
  isDeleting.value = false
}
</script>

<template>
  <div class="max-w-6xl mx-auto px-4 py-6 md:py-8">
    <div
      class="backdrop-blur-sm rounded-xl p-6 border"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <h1 class="text-white text-2xl mb-6 flex items-center gap-2">
        <Database class="w-6 h-6" style="color: #96EEF2" />
        Previous Dives
      </h1>

      <!-- Filter Section -->
      <div class="mb-6 grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label class="block text-sm mb-2" style="color: #96EEF2">Filter by Name</label>
          <div class="relative">
            <Search
              class="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4"
              style="color: #41B9C3"
            />
            <input
              v-model="nameFilter"
              type="text"
              placeholder="Search dive name..."
              class="w-full pl-10 pr-10 py-2 rounded-lg text-white placeholder-gray-400 focus:outline-none"
              style="background-color: rgba(14, 36, 70, 0.7); border: 1px solid rgba(65, 185, 195, 0.3)"
            />
            <button
              v-if="nameFilter"
              @click="nameFilter = ''"
              class="absolute right-3 top-1/2 transform -translate-y-1/2 hover:opacity-70 transition-opacity"
            >
              <X class="w-4 h-4" style="color: #96EEF2" />
            </button>
          </div>
        </div>

        <div>
          <label class="block text-sm mb-2" style="color: #96EEF2">Sort by Date</label>
          <div class="relative">
            <Calendar
              class="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4"
              style="color: #41B9C3"
            />
            <select
              v-model="dateSort"
              class="w-full pl-10 pr-4 py-2 rounded-lg text-white focus:outline-none appearance-none cursor-pointer"
              style="background-color: rgba(14, 36, 70, 0.7); border: 1px solid rgba(65, 185, 195, 0.3)"
            >
              <option value="recent">Recent</option>
              <option value="oldest">Oldest</option>
            </select>
          </div>
        </div>
      </div>

      <div
        v-if="error"
        class="mb-4 rounded-lg px-4 py-3 text-sm"
        style="background-color: rgba(221, 44, 29, 0.15); color: #FF4757; border: 1px solid rgba(221, 44, 29, 0.35)"
      >
        {{ error }}
      </div>

      <!-- Results Count -->
      <div
        v-if="nameFilter && !loading"
        class="mb-4 text-sm"
        style="color: #96EEF2"
      >
        Showing {{ filteredMissions.length }} of {{ previousMissions.length }} dives
      </div>

      <!-- Loading (initial) -->
      <div
        v-if="loading && previousMissions.length === 0"
        class="rounded-lg p-8 text-center"
        style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)"
      >
        <Database class="w-12 h-12 mx-auto mb-3" style="color: #41B9C3; opacity: 0.5" />
        <p class="text-white text-lg mb-2">Loading dives...</p>
        <p class="text-sm" style="color: #96EEF2">Fetching dive history</p>
      </div>

      <!-- Empty / no matches (not loading) -->
      <div
        v-else-if="filteredMissions.length === 0"
        class="rounded-lg p-8 text-center"
        style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)"
      >
        <Search class="w-12 h-12 mx-auto mb-3" style="color: #41B9C3; opacity: 0.5" />
        <p class="text-white text-lg mb-2">No dives found</p>
        <p v-if="nameFilter" class="text-sm" style="color: #96EEF2">Try adjusting your filters</p>
        <p v-else class="text-sm" style="color: #96EEF2">Start a dive from Home to create a record here.</p>
      </div>

      <!-- Dives List -->
      <div v-else class="space-y-3">
        <div
          v-for="mission in filteredMissions"
          :key="mission.id"
          class="rounded-lg p-5 transition-all hover:bg-slate-700/50"
          style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)"
        >
          <div class="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
            <div class="flex-1 min-w-0">
              <!-- Name + Status -->
              <div class="flex flex-wrap items-center gap-2 sm:gap-3 mb-3">
                <h3 class="text-white text-base sm:text-lg break-all">{{ mission.name }}</h3>
                <span
                  class="px-2 py-1 rounded text-xs flex-shrink-0"
                  style="background-color: rgba(252, 216, 105, 0.2); color: #FCD869"
                >
                  {{ mission.statusLabel }}
                </span>
              </div>
              <p
                v-if="mission.configuration"
                class="text-xs mb-3 break-all"
                style="color: #96EEF2"
              >
                Configuration: {{ mission.configuration }}
              </p>

              <!-- Info Grid -->
              <div class="grid grid-cols-1 md:grid-cols-4 gap-3 mb-3">
                <div class="flex items-center gap-2">
                  <Calendar class="w-4 h-4 flex-shrink-0" style="color: #96EEF2" />
                  <span class="text-sm" style="color: #96EEF2">
                    {{ mission.dateDisplay }}
                    <span class="mx-1 opacity-50">·</span>
                    <span style="color: #FCD869">{{ mission.timeDisplay }}</span>
                  </span>
                </div>
                <div class="flex items-center gap-2">
                  <Clock class="w-4 h-4" style="color: #96EEF2" />
                  <span class="text-sm" style="color: #96EEF2">{{ mission.duration }}</span>
                </div>
                <div class="flex items-center gap-2">
                  <MapPin class="w-4 h-4" style="color: #96EEF2" />
                  <span class="text-sm" style="color: #96EEF2">{{ mission.location }}</span>
                </div>
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="text-sm" style="color: #96EEF2">
                    Max depth: {{ mission.maxDepthLabel }}
                    <span
                      v-if="mission.maxDepthFromLog"
                      class="text-xs opacity-75 ml-1"
                    >(log)</span>
                  </span>
                </div>
              </div>

              <!-- Media Counts -->
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

            <!-- Action Buttons (vertical stack on right) -->
            <div class="flex flex-col gap-2 flex-shrink-0">
              <a
                v-if="mission.mcapRelativePath"
                :href="mcapDownloadHref(mission.mcapRelativePath)"
                class="px-4 py-2 rounded-lg flex items-center justify-center gap-2 text-sm whitespace-nowrap transition-all hover:opacity-90 no-underline"
                style="background-color: rgba(65, 185, 195, 0.25); border: 1px solid #41B9C3; color: #96EEF2"
                target="_blank"
                rel="noopener noreferrer"
              >
                <Archive class="w-4 h-4 flex-shrink-0" />
                Download MCAP
              </a>
              <span
                v-else
                class="px-4 py-2 rounded-lg flex items-center justify-center gap-2 text-sm whitespace-nowrap opacity-50 cursor-not-allowed"
                style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2); color: #96EEF2"
                title="No recorder .mcap matched this dive’s time window"
              >
                <Archive class="w-4 h-4 flex-shrink-0" />
                No MCAP linked
              </span>
              <a
                :href="scientificCsvHref(mission.id)"
                class="px-4 py-2 rounded-lg flex items-center justify-center gap-2 text-sm whitespace-nowrap transition-all hover:opacity-90 no-underline"
                style="background-color: rgba(0, 212, 170, 0.2); border: 1px solid rgba(0, 212, 170, 0.5); color: #00D4AA"
                :title="mission.mcapRelativePath ? 'Depth, temperature, GPS columns from recorder log where available' : 'Dive metadata and any samples we could read from the linked log'"
              >
                <Download class="w-4 h-4 flex-shrink-0" />
                Export scientific CSV
              </a>
              <button
                type="button"
                disabled
                title="Not available yet"
                class="px-4 py-2 rounded-lg flex items-center gap-2 text-sm whitespace-nowrap opacity-45 cursor-not-allowed"
                style="background-color: #41B9C3; color: white"
              >
                <FileText class="w-4 h-4" />
                View Log File
              </button>
              <button
                type="button"
                class="px-4 py-2 rounded-lg transition-all hover:opacity-90 flex items-center gap-2 text-sm whitespace-nowrap"
                style="background-color: #96EEF2; color: #0E2446"
                @click="emit('navigate', 'media')"
              >
                <Database class="w-4 h-4" />
                View Data Files
              </button>
              <button
                class="px-4 py-2 rounded-lg transition-all hover:opacity-90 flex items-center gap-2 text-sm whitespace-nowrap"
                style="background-color: #FF9937; color: white"
                @click="handleViewMedia(mission)"
              >
                <Play class="w-4 h-4" />
                View Media
              </button>
              <button
                type="button"
                disabled
                title="Use the Data page to download files"
                class="px-4 py-2 rounded-lg flex items-center gap-2 text-sm whitespace-nowrap opacity-45 cursor-not-allowed"
                style="background-color: rgba(65, 185, 195, 0.3); border: 1px solid #41B9C3; color: #96EEF2"
              >
                <Download class="w-4 h-4" />
                Download Media
              </button>
              <button
                type="button"
                disabled
                title="Not available yet"
                class="px-4 py-2 rounded-lg flex items-center gap-2 text-sm whitespace-nowrap opacity-45 cursor-not-allowed"
                style="background-color: rgba(65, 185, 195, 0.2); border: 1px solid #41B9C3; color: #41B9C3"
              >
                <Archive class="w-4 h-4" />
                Download All Files
              </button>
              <button
                class="px-4 py-2 rounded-lg transition-all hover:opacity-90 flex items-center gap-2 text-sm whitespace-nowrap"
                style="background-color: rgba(221, 44, 29, 0.2); border: 1px solid #DD2C1D; color: #DD2C1D"
                @click="selectedMission = mission.id"
              >
                <Trash2 class="w-4 h-4" />
                Delete Dive
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Delete Confirmation Modal -->
    <Teleport to="body">
      <div
        v-if="selectedMission !== null"
        class="fixed inset-0 z-50 flex items-center justify-center p-4"
        style="background-color: rgba(0, 0, 0, 0.7)"
        @click.self="selectedMission = null"
      >
        <div
          class="rounded-xl p-6 max-w-md w-full border shadow-2xl"
          style="background-color: #0E2446; border-color: #DD2C1D"
          @click.stop
        >
          <div class="flex items-start gap-4 mb-4">
            <div class="p-3 rounded-lg" style="background-color: rgba(221, 44, 29, 0.2)">
              <AlertTriangle class="w-6 h-6" style="color: #DD2C1D" />
            </div>
            <div class="flex-1">
              <h3 class="text-white text-xl mb-2">Delete Dive?</h3>
              <p style="color: #96EEF2" class="text-sm mb-2">
                Are you sure you want to delete this dive? This action cannot be undone.
              </p>
              <div class="rounded p-3 mt-3" style="background-color: rgba(65, 185, 195, 0.1)">
                <p class="text-white font-semibold mb-1">
                  {{ previousMissions.find((m: DisplayMission) => m.id === selectedMission)?.name }}
                </p>
                <p style="color: #96EEF2" class="text-xs">
                  {{ previousMissions.find((m: DisplayMission) => m.id === selectedMission)?.images }} images,
                  {{ previousMissions.find((m: DisplayMission) => m.id === selectedMission)?.videos }} videos
                </p>
              </div>
            </div>
          </div>

          <div class="flex gap-3 mt-6">
            <button
              @click="selectedMission = null"
              class="flex-1 px-4 py-2 rounded-lg transition-all hover:opacity-80"
              style="background-color: rgba(65, 185, 195, 0.3); border: 1px solid #41B9C3; color: #96EEF2"
            >
              Cancel
            </button>
            <button
              @click="handleDeleteMission"
              :disabled="isDeleting"
              class="flex-1 px-4 py-2 rounded-lg transition-all hover:opacity-90 flex items-center justify-center gap-2"
              style="background-color: #DD2C1D; color: white"
            >
              <Trash2 class="w-4 h-4" />
              {{ isDeleting ? 'Deleting...' : 'Delete Dive' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
