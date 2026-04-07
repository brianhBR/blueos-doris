<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  Download, FolderOpen, Image as ImageIcon, Video, FileText, Search,
  ArrowUpDown, ArrowUp, ArrowDown, Trash2, AlertTriangle, Play,
  ChevronLeft, ChevronRight, Loader2, AlertCircle, HardDrive,
} from 'lucide-vue-next'
import { useMedia, useStorage } from '../composables/useApi'
import { parseBackendDateTime } from '../parseBackendTime'

interface DisplayFile {
  id: string
  fileName: string
  type: 'video' | 'image' | 'sensor' | 'log' | 'system'
  diveName: string
  /** Local calendar date (e.g. Apr 4, 2026) */
  datePart: string
  /** 24-hour hh:mm UTC */
  timePart: string
  /** UTC instant for sorting (ms since epoch) */
  createdMs: number
  size: string
  downloadUrl: string
}

const { files: apiFiles, loading: mediaLoading, error: mediaError, fetchFiles, deleteFile, downloadFile } = useMedia()
const { storage, loading: storageLoading, error: storageError, fetchStorage } = useStorage()

type SortField = 'diveName' | 'fileName' | 'date' | 'type'
type SortDirection = 'asc' | 'desc' | null

const searchQuery = ref('')
const sortField = ref<SortField>('date')
const sortDirection = ref<SortDirection>('desc')
const selectedFiles = ref<string[]>([])
const showDeleteConfirm = ref(false)
const currentPage = ref(1)
const itemsPerPage = ref(10)
const showEraseAllConfirm = ref(false)
const eraseAllStep = ref<1 | 2>(1)
const isDeleting = ref(false)
const initialLoadDone = ref(false)

function formatFileSize(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`
  if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(0)} KB`
  return `${bytes} B`
}

function mapMediaType(mediaType: string): DisplayFile['type'] {
  if (mediaType === 'video') return 'video'
  if (mediaType === 'image') return 'image'
  if (mediaType === 'data') return 'sensor'
  if (mediaType === 'system') return 'system'
  return 'log'
}

function parseMediaCreatedAt(iso: string): Date {
  const d = parseBackendDateTime(iso)
  return Number.isNaN(d.getTime()) ? new Date(0) : d
}

/** Calendar date + 24-hour clock in UTC (backend stores UTC). */
function formatMediaDateParts(d: Date): { datePart: string; timePart: string } {
  const utc: Intl.DateTimeFormatOptions = { timeZone: 'UTC' }
  return {
    datePart: d.toLocaleDateString(undefined, {
      ...utc,
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    }),
    timePart:
      d.toLocaleTimeString(undefined, {
        ...utc,
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }) + ' UTC',
  }
}

const mediaFiles = computed<DisplayFile[]>(() => {
  return apiFiles.value.map((f) => {
    const createdDate = parseMediaCreatedAt(f.created_at)
    const { datePart, timePart } = formatMediaDateParts(createdDate)
    return {
      id: f.id,
      fileName: f.filename,
      type: mapMediaType(f.media_type),
      diveName: (f.dive_name && f.dive_name.trim()) ? f.dive_name.trim() : '—',
      datePart,
      timePart,
      createdMs: createdDate.getTime(),
      size: formatFileSize(f.size_bytes),
      downloadUrl: f.download_url,
    }
  })
})

const storageTotalGb = computed(() => storage.value?.total_gb ?? 0)
const storageAvailableGb = computed(() => storage.value?.available_gb ?? 0)
const storageUsedPercent = computed(() => storage.value?.used_percent ?? 0)
const storageAvailablePercent = computed(() => storageTotalGb.value > 0 ? Math.round(100 - storageUsedPercent.value) : 0)
const hasStorageData = computed(() => storage.value !== null)
const storageType = computed(() => storage.value?.storage_type ?? 'SD Card')

let pollInterval: number | undefined

onMounted(async () => {
  await Promise.all([fetchFiles(), fetchStorage()])
  initialLoadDone.value = true
  pollInterval = setInterval(() => {
    fetchFiles()
    fetchStorage()
  }, 15000) as unknown as number
})

onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval)
})

const getFileIcon = (type: string) => {
  switch (type) {
    case 'video': return Video
    case 'image': return ImageIcon
    case 'system': return HardDrive
    default: return FileText
  }
}

const getFileIconColor = (type: string) => {
  switch (type) {
    case 'video': return '#c084fc'
    case 'image': return '#60a5fa'
    case 'sensor': return '#4ade80'
    case 'system': return '#94a3b8'
    case 'log': return '#FFC107'
    default: return '#96EEF2'
  }
}

const getTypeLabel = (type: string) => {
  switch (type) {
    case 'video': return 'Video'
    case 'image': return 'Image'
    case 'sensor': return 'Data'
    case 'system': return 'System'
    case 'log': return 'Log'
    default: return type
  }
}

const getTypeBadgeStyle = (type: string) => {
  switch (type) {
    case 'video': return { backgroundColor: 'rgba(168, 85, 247, 0.2)', color: '#c084fc' }
    case 'image': return { backgroundColor: 'rgba(59, 130, 246, 0.2)', color: '#60a5fa' }
    case 'sensor': return { backgroundColor: 'rgba(34, 197, 94, 0.2)', color: '#4ade80' }
    case 'system': return { backgroundColor: 'rgba(148, 163, 184, 0.2)', color: '#94a3b8' }
    case 'log': return { backgroundColor: 'rgba(255, 193, 7, 0.2)', color: '#FFC107' }
    default: return { backgroundColor: 'rgba(150, 238, 242, 0.2)', color: '#96EEF2' }
  }
}

const filteredFiles = computed(() => {
  return mediaFiles.value.filter(file =>
    file.fileName.toLowerCase().includes(searchQuery.value.toLowerCase()) ||
    file.diveName.toLowerCase().includes(searchQuery.value.toLowerCase())
  )
})

const sortedFiles = computed(() => {
  const files = [...filteredFiles.value]
  if (!sortDirection.value) return files

  return files.sort((a, b) => {
    let aVal: string | number = ''
    let bVal: string | number = ''

    switch (sortField.value) {
      case 'diveName': aVal = a.diveName; bVal = b.diveName; break
      case 'fileName': aVal = a.fileName; bVal = b.fileName; break
      case 'date': aVal = a.createdMs; bVal = b.createdMs; break
      case 'type': aVal = a.type; bVal = b.type; break
    }

    if (sortDirection.value === 'asc') return aVal > bVal ? 1 : -1
    return aVal < bVal ? 1 : -1
  })
})

const totalPages = computed(() => Math.max(1, Math.ceil(sortedFiles.value.length / itemsPerPage.value)))

const currentFiles = computed(() => {
  const start = (currentPage.value - 1) * itemsPerPage.value
  return sortedFiles.value.slice(start, start + itemsPerPage.value)
})

const handleSort = (field: SortField) => {
  if (sortField.value === field) {
    if (sortDirection.value === 'asc') sortDirection.value = 'desc'
    else if (sortDirection.value === 'desc') { sortDirection.value = null; sortField.value = 'date' }
    else sortDirection.value = 'asc'
  } else {
    sortField.value = field
    sortDirection.value = 'asc'
  }
}

const handleSelectFile = (id: string) => {
  const idx = selectedFiles.value.indexOf(id)
  if (idx >= 0) selectedFiles.value.splice(idx, 1)
  else selectedFiles.value.push(id)
}

const handleSelectAll = () => {
  if (selectedFiles.value.length === sortedFiles.value.length) {
    selectedFiles.value = []
  } else {
    selectedFiles.value = sortedFiles.value.map(f => f.id)
  }
}

const confirmDelete = async () => {
  isDeleting.value = true
  for (const fileId of selectedFiles.value) {
    await deleteFile(fileId)
  }
  selectedFiles.value = []
  showDeleteConfirm.value = false
  isDeleting.value = false
  await fetchFiles()
}

const handleDownloadSelected = () => {
  for (const fileId of selectedFiles.value) {
    const file = mediaFiles.value.find(f => f.id === fileId)
    if (file) downloadFile(fileId, file.fileName)
  }
}

const handleDownloadSingle = (file: DisplayFile) => {
  downloadFile(file.id, file.fileName)
}

const handleDeleteSingle = (file: DisplayFile) => {
  selectedFiles.value = [file.id]
  showDeleteConfirm.value = true
}

const confirmEraseAll = async () => {
  isDeleting.value = true
  for (const file of mediaFiles.value) {
    await deleteFile(file.id)
  }
  selectedFiles.value = []
  showEraseAllConfirm.value = false
  eraseAllStep.value = 1
  isDeleting.value = false
  await fetchFiles()
}

const handlePageChange = (page: number) => {
  if (page >= 1 && page <= totalPages.value) currentPage.value = page
}
</script>

<template>
  <div class="max-w-7xl mx-auto px-4 py-6 md:py-8">
    <div
      class="backdrop-blur-sm rounded-xl p-4 md:p-6 border"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <div class="flex flex-col md:flex-row md:items-center md:justify-between mb-6 gap-4">
        <h1 class="text-white text-xl md:text-2xl flex items-center gap-2">
          <FolderOpen class="w-5 h-5 md:w-6 md:h-6" style="color: #96EEF2" />
          Data Management
        </h1>
      </div>

      <!-- Error Banner -->
      <div
        v-if="mediaError || storageError"
        class="rounded-lg p-3 md:p-4 mb-4 md:mb-6 flex items-start gap-3"
        style="background-color: rgba(221, 44, 29, 0.15); border: 1px solid rgba(221, 44, 29, 0.4)"
      >
        <AlertCircle class="w-5 h-5 flex-shrink-0 mt-0.5" style="color: #DD2C1D" />
        <div>
          <p class="text-sm font-semibold" style="color: #DD2C1D">Unable to fetch data from the system</p>
          <p v-if="mediaError" class="text-xs mt-1" style="color: #FF6B6B">Files: {{ mediaError }}</p>
          <p v-if="storageError" class="text-xs mt-1" style="color: #FF6B6B">Storage: {{ storageError }}</p>
        </div>
      </div>

      <!-- Storage Info -->
      <div
        class="rounded-lg p-3 md:p-4 mb-4 md:mb-6"
        style="background-color: rgba(65, 185, 195, 0.1); border: 1px solid rgba(65, 185, 195, 0.3)"
      >
        <div v-if="storageLoading && !hasStorageData" class="flex items-center gap-2">
          <Loader2 class="w-4 h-4 animate-spin" style="color: #41B9C3" />
          <span class="text-sm" style="color: #96EEF2">Loading storage info...</span>
        </div>
        <div v-else class="flex flex-col md:flex-row items-start md:items-center justify-between gap-3 md:gap-4">
          <div>
            <p class="text-sm md:text-base" style="color: #96EEF2">
              Total Storage: {{ hasStorageData ? storageTotalGb.toFixed(1) + ' GB' : 'Unavailable' }}
              <span v-if="hasStorageData" class="text-xs px-2 py-0.5 rounded-full ml-1" style="background-color: rgba(65, 185, 195, 0.2); color: #96EEF2">{{ storageType }}</span>
            </p>
            <p class="text-sm md:text-base" style="color: #41B9C3">
              Available: {{ hasStorageData ? storageAvailableGb.toFixed(1) + ' GB (' + storageAvailablePercent + '%)' : 'Unavailable' }}
            </p>
            <div v-if="hasStorageData" class="w-full md:w-48 rounded-full h-2 mt-2" style="background-color: rgba(14, 36, 70, 0.6)">
              <div
                class="h-2 rounded-full transition-all"
                :style="{
                  width: `${storageUsedPercent}%`,
                  background: storageUsedPercent > 90 ? 'linear-gradient(90deg, #DD2C1D 0%, #FF4757 100%)' : storageUsedPercent > 70 ? 'linear-gradient(90deg, #FF9937 0%, #FFB800 100%)' : 'linear-gradient(90deg, #41B9C3 0%, #96EEF2 100%)'
                }"
              />
            </div>
          </div>
          <div class="text-left md:text-right">
            <p class="text-sm md:text-base" style="color: #96EEF2">Total Files: {{ mediaFiles.length }}</p>
            <p v-if="mediaFiles.length > 0" class="text-sm md:text-base" style="color: #96EEF2">
              {{ mediaFiles.filter((f: DisplayFile) => f.type === 'video').length }} videos,
              {{ mediaFiles.filter((f: DisplayFile) => f.type === 'image').length }} images,
              {{ mediaFiles.filter((f: DisplayFile) => f.type === 'sensor').length }} sensor,
              {{ mediaFiles.filter((f: DisplayFile) => f.type === 'system').length }} system
            </p>
          </div>
        </div>
      </div>

      <!-- Search Bar and Batch Actions -->
      <div class="flex flex-col gap-3 mb-4 md:mb-6">
        <div class="flex-1 relative">
          <Search
            class="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 md:w-5 md:h-5"
            style="color: #41B9C3"
          />
          <input
            v-model="searchQuery"
            type="text"
            placeholder="Search by dive name or file name..."
            class="w-full pl-9 md:pl-10 pr-4 py-2 md:py-2.5 text-sm md:text-base text-white rounded-lg focus:outline-none"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
            @input="currentPage = 1"
          />
        </div>
        <div v-if="selectedFiles.length > 0" class="flex gap-2">
          <button
            @click="handleDownloadSelected"
            class="flex-1 md:flex-none px-3 md:px-4 py-2 rounded-lg transition-all hover:opacity-90 flex items-center justify-center gap-2 text-sm whitespace-nowrap"
            style="background-color: #41B9C3; color: white"
          >
            <Download class="w-4 h-4" />
            <span class="hidden sm:inline">Download</span> ({{ selectedFiles.length }})
          </button>
          <button
            @click="showDeleteConfirm = true"
            class="flex-1 md:flex-none px-3 md:px-4 py-2 rounded-lg transition-all hover:opacity-90 flex items-center justify-center gap-2 text-sm whitespace-nowrap"
            style="background-color: #DD2C1D; color: white"
          >
            <Trash2 class="w-4 h-4" />
            <span class="hidden sm:inline">Delete</span> ({{ selectedFiles.length }})
          </button>
        </div>
      </div>

      <!-- Desktop Table View -->
      <div class="hidden lg:block overflow-x-auto">
        <table class="w-full">
          <thead>
            <tr style="border-bottom: 2px solid rgba(65, 185, 195, 0.3)">
              <th class="text-left p-3 w-10">
                <input
                  type="checkbox"
                  :checked="selectedFiles.length === sortedFiles.length && sortedFiles.length > 0"
                  @change="handleSelectAll"
                  class="w-4 h-4 cursor-pointer"
                  style="accent-color: #41B9C3"
                />
              </th>
              <th class="text-left p-3">
                <button
                  @click="handleSort('diveName')"
                  class="flex items-center gap-2 hover:opacity-80 transition-opacity"
                  :style="{ color: sortField === 'diveName' ? '#41B9C3' : '#96EEF2' }"
                >
                  Dive Name
                  <ArrowUp v-if="sortField === 'diveName' && sortDirection === 'asc'" class="w-4 h-4" />
                  <ArrowDown v-else-if="sortField === 'diveName' && sortDirection === 'desc'" class="w-4 h-4" />
                  <ArrowUpDown v-else class="w-4 h-4" />
                </button>
              </th>
              <th class="text-left p-3">
                <button
                  @click="handleSort('fileName')"
                  class="flex items-center gap-2 hover:opacity-80 transition-opacity"
                  :style="{ color: sortField === 'fileName' ? '#41B9C3' : '#96EEF2' }"
                >
                  File Name
                  <ArrowUp v-if="sortField === 'fileName' && sortDirection === 'asc'" class="w-4 h-4" />
                  <ArrowDown v-else-if="sortField === 'fileName' && sortDirection === 'desc'" class="w-4 h-4" />
                  <ArrowUpDown v-else class="w-4 h-4" />
                </button>
              </th>
              <th class="text-left p-3">
                <button
                  @click="handleSort('date')"
                  class="flex items-center gap-2 hover:opacity-80 transition-opacity"
                  :style="{ color: sortField === 'date' ? '#41B9C3' : '#96EEF2' }"
                >
                  Date &amp; time (UTC)
                  <ArrowUp v-if="sortField === 'date' && sortDirection === 'asc'" class="w-4 h-4" />
                  <ArrowDown v-else-if="sortField === 'date' && sortDirection === 'desc'" class="w-4 h-4" />
                  <ArrowUpDown v-else class="w-4 h-4" />
                </button>
              </th>
              <th class="text-left p-3">
                <button
                  @click="handleSort('type')"
                  class="flex items-center gap-2 hover:opacity-80 transition-opacity"
                  :style="{ color: sortField === 'type' ? '#41B9C3' : '#96EEF2' }"
                >
                  Type
                  <ArrowUp v-if="sortField === 'type' && sortDirection === 'asc'" class="w-4 h-4" />
                  <ArrowDown v-else-if="sortField === 'type' && sortDirection === 'desc'" class="w-4 h-4" />
                  <ArrowUpDown v-else class="w-4 h-4" />
                </button>
              </th>
              <th class="text-left p-3" style="color: #96EEF2">Size</th>
              <th class="text-right p-3" style="color: #96EEF2">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(file, index) in currentFiles"
              :key="file.id"
              class="transition-all hover:bg-opacity-50"
              :style="{
                borderBottom: index < currentFiles.length - 1 ? '1px solid rgba(65, 185, 195, 0.15)' : 'none',
                backgroundColor: selectedFiles.includes(file.id)
                  ? 'rgba(65, 185, 195, 0.15)'
                  : index % 2 === 0
                    ? 'rgba(14, 36, 70, 0.2)'
                    : 'transparent'
              }"
            >
              <td class="p-3">
                <input
                  type="checkbox"
                  :checked="selectedFiles.includes(file.id)"
                  @change="handleSelectFile(file.id)"
                  class="w-4 h-4 cursor-pointer"
                  style="accent-color: #41B9C3"
                />
              </td>
              <td class="p-3 text-white">{{ file.diveName }}</td>
              <td class="p-3">
                <div class="flex items-center gap-3">
                  <component :is="getFileIcon(file.type)" class="w-5 h-5" :style="{ color: getFileIconColor(file.type) }" />
                  <span class="text-white">{{ file.fileName }}</span>
                </div>
              </td>
              <td class="p-3 whitespace-nowrap" style="color: #96EEF2">
                <span>{{ file.datePart }}</span>
                <span class="mx-1 opacity-50">·</span>
                <span style="color: #FCD869">{{ file.timePart }}</span>
              </td>
              <td class="p-3">
                <span
                  class="px-2 py-1 rounded text-xs"
                  :style="getTypeBadgeStyle(file.type)"
                >
                  {{ getTypeLabel(file.type) }}
                </span>
              </td>
              <td class="p-3" style="color: #96EEF2">{{ file.size }}</td>
              <td class="p-3">
                <div class="flex justify-end gap-2">
                  <span title="Work in progress">
                    <button
                      disabled
                      class="px-3 py-1.5 rounded-lg flex items-center gap-2 text-sm opacity-40 cursor-not-allowed pointer-events-none"
                      style="background-color: #FF9937; color: white"
                    >
                      <Play class="w-3.5 h-3.5" />
                      View
                    </button>
                  </span>
                  <button
                    @click="handleDownloadSingle(file)"
                    class="px-3 py-1.5 rounded-lg transition-all hover:opacity-80 flex items-center gap-2 text-sm"
                    style="background-color: #41B9C3; color: white"
                  >
                    <Download class="w-3.5 h-3.5" />
                    Download
                  </button>
                  <button
                    @click="handleDeleteSingle(file)"
                    class="px-3 py-1.5 rounded-lg transition-all hover:opacity-80 flex items-center gap-2 text-sm"
                    style="background-color: rgba(221, 44, 29, 0.8); color: white"
                  >
                    <Trash2 class="w-3.5 h-3.5" />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="mediaLoading && mediaFiles.length === 0" class="text-center py-12">
          <Loader2 class="w-8 h-8 mx-auto mb-3 animate-spin" style="color: #41B9C3" />
          <p style="color: #96EEF2">Loading files...</p>
        </div>
        <div v-else-if="sortedFiles.length === 0 && !mediaLoading" class="text-center py-12">
          <FolderOpen class="w-10 h-10 mx-auto mb-3" style="color: #41B9C3; opacity: 0.5" />
          <p class="text-white mb-1">{{ searchQuery ? 'No matching files' : 'No files found' }}</p>
          <p class="text-sm" style="color: #96EEF2">{{ searchQuery ? 'Try a different search term' : 'Files from dives will appear here' }}</p>
        </div>
      </div>

      <!-- Mobile Card View -->
      <div class="lg:hidden space-y-3">
        <!-- Sort Controls -->
        <div class="flex items-center gap-2 pb-3 border-b" style="border-color: rgba(65, 185, 195, 0.3)">
          <span class="text-sm" style="color: #96EEF2">Sort by:</span>
          <select
            :value="sortField"
            @change="(e: Event) => { sortField = (e.target as HTMLSelectElement).value as SortField; sortDirection = 'asc' }"
            class="flex-1 px-3 py-2 text-sm text-white rounded-lg focus:outline-none"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
          >
            <option value="date" style="background-color: #0E2446">Date &amp; time (UTC)</option>
            <option value="diveName" style="background-color: #0E2446">Dive Name</option>
            <option value="fileName" style="background-color: #0E2446">File Name</option>
            <option value="type" style="background-color: #0E2446">Type</option>
          </select>
          <button
            @click="sortDirection = sortDirection === 'asc' ? 'desc' : 'asc'"
            class="px-3 py-2 rounded-lg transition-all"
            style="background-color: rgba(65, 185, 195, 0.3); border: 1px solid rgba(65, 185, 195, 0.3)"
          >
            <ArrowUp v-if="sortDirection === 'asc'" class="w-4 h-4" style="color: #96EEF2" />
            <ArrowDown v-else class="w-4 h-4" style="color: #96EEF2" />
          </button>
        </div>

        <!-- Select All Checkbox -->
        <div
          v-if="sortedFiles.length > 0"
          class="flex items-center gap-2 px-3 py-2 rounded-lg"
          style="background-color: rgba(65, 185, 195, 0.1)"
        >
          <input
            type="checkbox"
            :checked="selectedFiles.length === sortedFiles.length && sortedFiles.length > 0"
            @change="handleSelectAll"
            class="w-4 h-4 cursor-pointer"
            style="accent-color: #41B9C3"
          />
          <span class="text-sm" style="color: #96EEF2">
            Select All ({{ sortedFiles.length }})
          </span>
        </div>

        <!-- File Cards -->
        <div
          v-for="file in currentFiles"
          :key="file.id"
          class="rounded-lg p-4 border"
          :style="{
            backgroundColor: selectedFiles.includes(file.id)
              ? 'rgba(65, 185, 195, 0.15)'
              : 'rgba(14, 36, 70, 0.3)',
            borderColor: selectedFiles.includes(file.id)
              ? 'rgba(65, 185, 195, 0.5)'
              : 'rgba(65, 185, 195, 0.2)'
          }"
        >
          <div class="flex items-start gap-3 mb-3">
            <input
              type="checkbox"
              :checked="selectedFiles.includes(file.id)"
              @change="handleSelectFile(file.id)"
              class="w-5 h-5 cursor-pointer mt-0.5 flex-shrink-0"
              style="accent-color: #41B9C3"
            />
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 mb-2">
                <component :is="getFileIcon(file.type)" class="w-5 h-5" :style="{ color: getFileIconColor(file.type) }" />
                <span
                  class="px-2 py-1 rounded text-xs"
                  :style="getTypeBadgeStyle(file.type)"
                >
                  {{ getTypeLabel(file.type) }}
                </span>
              </div>
              <h3 class="text-white font-medium mb-1 text-sm break-all">{{ file.fileName }}</h3>
              <p class="text-xs mb-2 break-all" style="color: #96EEF2">{{ file.diveName }}</p>
              <div class="flex flex-col gap-0.5 text-xs mb-3" style="color: #41B9C3">
                <span style="color: #96EEF2">
                  {{ file.datePart }}
                  <span class="mx-1 opacity-50">·</span>
                  <span style="color: #FCD869">{{ file.timePart }}</span>
                </span>
                <span>{{ file.size }}</span>
              </div>
              <div class="flex gap-2">
                <span class="flex-1" title="Work in progress">
                  <button
                    disabled
                    class="w-full px-3 py-2 rounded-lg flex items-center justify-center gap-2 text-sm opacity-40 cursor-not-allowed pointer-events-none"
                    style="background-color: #FF9937; color: white"
                  >
                    <Play class="w-3.5 h-3.5" />
                    View
                  </button>
                </span>
                <button
                  @click="handleDownloadSingle(file)"
                  class="flex-1 px-3 py-2 rounded-lg transition-all hover:opacity-80 flex items-center justify-center gap-2 text-sm"
                  style="background-color: #41B9C3; color: white"
                >
                  <Download class="w-3.5 h-3.5" />
                  Download
                </button>
                <button
                  @click="handleDeleteSingle(file)"
                  class="px-3 py-2 rounded-lg transition-all hover:opacity-80 flex items-center justify-center text-sm"
                  style="background-color: rgba(221, 44, 29, 0.8); color: white"
                >
                  <Trash2 class="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          </div>
        </div>

        <div v-if="mediaLoading && mediaFiles.length === 0" class="text-center py-12">
          <Loader2 class="w-8 h-8 mx-auto mb-3 animate-spin" style="color: #41B9C3" />
          <p style="color: #96EEF2">Loading files...</p>
        </div>
        <div v-else-if="sortedFiles.length === 0 && !mediaLoading" class="text-center py-12">
          <FolderOpen class="w-10 h-10 mx-auto mb-3" style="color: #41B9C3; opacity: 0.5" />
          <p class="text-white mb-1">{{ searchQuery ? 'No matching files' : 'No files found' }}</p>
          <p class="text-sm" style="color: #96EEF2">{{ searchQuery ? 'Try a different search term' : 'Files from dives will appear here' }}</p>
        </div>
      </div>

      <!-- Pagination -->
      <div class="flex flex-col sm:flex-row items-center justify-between mt-4 gap-3">
        <div class="flex items-center gap-2">
          <label class="text-xs sm:text-sm" style="color: #96EEF2">Per page:</label>
          <select
            v-model.number="itemsPerPage"
            @change="currentPage = 1"
            class="px-2 py-1 rounded-lg text-sm text-white"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3); color: #96EEF2"
          >
            <option :value="10" style="background-color: #0E2446">10</option>
            <option :value="20" style="background-color: #0E2446">20</option>
            <option :value="50" style="background-color: #0E2446">50</option>
            <option :value="100" style="background-color: #0E2446">100</option>
          </select>
        </div>
        <div class="flex items-center gap-2">
          <button
            @click="handlePageChange(currentPage - 1)"
            :disabled="currentPage === 1"
            class="px-3 py-1.5 rounded-lg transition-all hover:opacity-80 disabled:opacity-50 disabled:cursor-not-allowed"
            style="background-color: rgba(65, 185, 195, 0.3); border: 1px solid #41B9C3; color: #96EEF2"
          >
            <ChevronLeft class="w-4 h-4" />
          </button>
          <span class="text-xs sm:text-sm whitespace-nowrap" style="color: #96EEF2">
            Page {{ currentPage }} of {{ totalPages }}
          </span>
          <button
            @click="handlePageChange(currentPage + 1)"
            :disabled="currentPage === totalPages"
            class="px-3 py-1.5 rounded-lg transition-all hover:opacity-80 disabled:opacity-50 disabled:cursor-not-allowed"
            style="background-color: rgba(65, 185, 195, 0.3); border: 1px solid #41B9C3; color: #96EEF2"
          >
            <ChevronRight class="w-4 h-4" />
          </button>
        </div>
      </div>

      <!-- Results count -->
      <div class="mt-4 text-xs sm:text-sm" style="color: #96EEF2">
        Showing {{ sortedFiles.length }} of {{ mediaFiles.length }} files
        <span v-if="selectedFiles.length > 0"> &middot; {{ selectedFiles.length }} selected</span>
        <span v-if="sortField && sortDirection" class="hidden sm:inline">
          &middot; Sorted by {{ sortField }} ({{ sortDirection === 'asc' ? 'ascending' : 'descending' }})
        </span>
      </div>

      <!-- Erase All Files Section -->
      <div
        class="mt-6 rounded-lg p-4 border"
        style="background-color: rgba(221, 44, 29, 0.1); border-color: rgba(221, 44, 29, 0.3)"
      >
        <button
          @click="showEraseAllConfirm = true; eraseAllStep = 1"
          class="px-4 py-2 rounded-lg transition-all hover:opacity-90 flex items-center gap-2 mb-2 text-sm"
          style="background-color: #DD2C1D; color: white"
        >
          <Trash2 class="w-4 h-4" />
          Erase All Files
        </button>
        <p class="text-xs sm:text-sm" style="color: #96EEF2">
          Permanently erase all files from the system. This action cannot be undone.
        </p>
      </div>
    </div>

    <!-- Delete Confirmation Modal -->
    <Teleport to="body">
      <div
        v-if="showDeleteConfirm"
        class="fixed inset-0 z-50 flex items-center justify-center p-4"
        style="background-color: rgba(0, 0, 0, 0.7)"
        @click.self="showDeleteConfirm = false"
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
              <h3 class="text-white text-xl mb-2">Delete Selected Files?</h3>
              <p style="color: #96EEF2" class="text-sm mb-2">
                Are you sure you want to delete {{ selectedFiles.length }} file{{ selectedFiles.length > 1 ? 's' : '' }}? This action cannot be undone.
              </p>
              <div class="rounded p-3 mt-3 max-h-40 overflow-y-auto" style="background-color: rgba(65, 185, 195, 0.1)">
                <p
                  v-for="file in mediaFiles.filter((f: DisplayFile) => selectedFiles.includes(f.id))"
                  :key="file.id"
                  class="text-white text-xs mb-1"
                >
                  {{ file.fileName }}
                </p>
              </div>
            </div>
          </div>

          <div class="flex gap-3 mt-6">
            <button
              @click="showDeleteConfirm = false"
              class="flex-1 px-4 py-2 rounded-lg transition-all hover:opacity-80"
              style="background-color: rgba(65, 185, 195, 0.3); border: 1px solid #41B9C3; color: #96EEF2"
            >
              Cancel
            </button>
            <button
              @click="confirmDelete"
              :disabled="isDeleting"
              class="flex-1 px-4 py-2 rounded-lg transition-all hover:opacity-90 flex items-center justify-center gap-2"
              style="background-color: #DD2C1D; color: white"
            >
              <Trash2 class="w-4 h-4" />
              {{ isDeleting ? 'Deleting...' : 'Delete Files' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Erase All Confirmation Modal -->
    <Teleport to="body">
      <div
        v-if="showEraseAllConfirm"
        class="fixed inset-0 z-50 flex items-center justify-center p-4"
        style="background-color: rgba(0, 0, 0, 0.7)"
        @click.self="showEraseAllConfirm = false"
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
              <h3 class="text-white text-xl mb-2">Erase All Files?</h3>
              <p style="color: #96EEF2" class="text-sm mb-2">
                Are you sure you want to erase all {{ mediaFiles.length }} file{{ mediaFiles.length !== 1 ? 's' : '' }}? This action will reformat the DORIS drive and cannot be undone.
              </p>
              <div
                v-if="eraseAllStep === 1"
                class="rounded p-3 mt-3 max-h-40 overflow-y-auto"
                style="background-color: rgba(65, 185, 195, 0.1)"
              >
                <p
                  v-for="file in mediaFiles"
                  :key="file.id"
                  class="text-white text-xs mb-1"
                >
                  {{ file.fileName }}
                </p>
              </div>
              <div
                v-else
                class="rounded p-3 mt-3"
                style="background-color: rgba(65, 185, 195, 0.1)"
              >
                <p class="text-white text-sm">All files will be permanently deleted.</p>
              </div>
            </div>
          </div>

          <div class="flex gap-3 mt-6">
            <button
              @click="showEraseAllConfirm = false"
              class="flex-1 px-4 py-2 rounded-lg transition-all hover:opacity-80"
              style="background-color: rgba(65, 185, 195, 0.3); border: 1px solid #41B9C3; color: #96EEF2"
            >
              Cancel
            </button>
            <button
              @click="eraseAllStep === 1 ? eraseAllStep = 2 : confirmEraseAll()"
              :disabled="isDeleting"
              class="flex-1 px-4 py-2 rounded-lg transition-all hover:opacity-90 flex items-center justify-center gap-2"
              style="background-color: #DD2C1D; color: white"
            >
              <Loader2 v-if="isDeleting" class="w-4 h-4 animate-spin" />
              <Trash2 v-else class="w-4 h-4" />
              {{ isDeleting ? 'Erasing...' : eraseAllStep === 1 ? 'Confirm Erase' : 'Erase All Files' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
