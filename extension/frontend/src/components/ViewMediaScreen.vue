<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  ArrowLeft, Calendar, Clock, Gauge, MapPin, User, Download,
  Trash2, ZoomIn, Image, Video, Play, FileText, Loader2, AlertCircle
} from 'lucide-vue-next'
import type { Screen, DiveData } from '../types'
import { useMedia, type MediaFile } from '../composables/useApi'
import { parseBackendDateTime } from '../parseBackendTime'

const API_V1 = '/api/v1'

interface GalleryItem {
  id: string
  type: 'image' | 'video'
  url: string
  thumbnail: string
  timestamp: string
  depth: string
  duration?: string
  createdMs: number
}

const props = withDefaults(defineProps<{
  previousScreen?: Screen
  diveData?: DiveData | null
}>(), {
  previousScreen: 'media',
  diveData: null
})

const emit = defineEmits<{
  navigate: [screen: Screen]
}>()

const { files, loading, error, fetchFiles } = useMedia()

const lightboxIndex = ref<number | null>(null)
const showDeleteModal = ref(false)

const currentDive = computed<DiveData>(() => props.diveData ?? {
  name: 'Media',
  date: '—',
  duration: '—',
  maxDepth: '—',
  location: '—',
  operator: undefined,
  images: undefined,
  videos: undefined
})

function mediaDownloadUrl(fileId: string): string {
  return `${API_V1}/media/download?${new URLSearchParams({ path: fileId }).toString()}`
}

function formatVideoDuration(seconds: number | null): string | undefined {
  if (seconds == null || seconds <= 0) return undefined
  const s = Math.floor(seconds)
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${String(r).padStart(2, '0')}`
}

function fileToGalleryItem(f: MediaFile): GalleryItem | null {
  if (f.media_type !== 'image' && f.media_type !== 'video') return null
  const url = mediaDownloadUrl(f.id)
  const d = parseBackendDateTime(f.created_at)
  const createdMs = Number.isNaN(d.getTime()) ? 0 : d.getTime()
  const ts = Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleTimeString(undefined, {
        timeZone: 'UTC',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }) + ' UTC'
  return {
    id: f.id,
    type: f.media_type,
    url,
    thumbnail: url,
    timestamp: ts,
    depth: '—',
    duration: f.media_type === 'video' ? formatVideoDuration(f.duration_seconds) : undefined,
    createdMs,
  }
}

const diveNameNorm = computed(() => props.diveData?.name?.trim().toLowerCase() ?? '')

const mediaItems = computed<GalleryItem[]>(() => {
  const want = diveNameNorm.value
  const rows = files.value
    .filter((f) => {
      if (f.media_type !== 'image' && f.media_type !== 'video') return false
      if (!want) return true
      const dn = (f.dive_name ?? '').trim().toLowerCase()
      return dn === want
    })
    .map(fileToGalleryItem)
    .filter((x): x is GalleryItem => x != null)
  rows.sort((a, b) => b.createdMs - a.createdMs)
  return rows
})

const lightboxItem = computed(() => {
  if (lightboxIndex.value === null) return null
  return mediaItems.value[lightboxIndex.value]
})

const openLightbox = (index: number) => {
  lightboxIndex.value = index
}

const closeLightbox = () => {
  lightboxIndex.value = null
}

const prevImage = () => {
  if (lightboxIndex.value !== null && lightboxIndex.value > 0) {
    lightboxIndex.value--
  }
}

const nextImage = () => {
  if (lightboxIndex.value !== null && lightboxIndex.value < mediaItems.value.length - 1) {
    lightboxIndex.value++
  }
}

const handleKeydown = (e: KeyboardEvent) => {
  if (lightboxIndex.value === null) return
  if (e.key === 'ArrowLeft') prevImage()
  if (e.key === 'ArrowRight') nextImage()
  if (e.key === 'Escape') closeLightbox()
}

const confirmDelete = () => {
  showDeleteModal.value = true
}

const executeDelete = () => {
  showDeleteModal.value = false
  emit('navigate', props.previousScreen)
}

const cancelDelete = () => {
  showDeleteModal.value = false
}

const goBack = () => {
  emit('navigate', props.previousScreen)
}

const imageCount = computed(() => mediaItems.value.filter(m => m.type === 'image').length)
const videoCount = computed(() => mediaItems.value.filter(m => m.type === 'video').length)

onMounted(() => {
  void fetchFiles({ limit: 500 })
})
</script>

<template>
  <div
    class="max-w-7xl mx-auto px-4 py-6 md:py-8"
    @keydown="handleKeydown"
    tabindex="0"
  >
    <!-- Back Button -->
    <button
      @click="goBack"
      class="flex items-center gap-2 mb-6 transition-colors hover:opacity-80"
      style="color: #41B9C3"
    >
      <ArrowLeft class="w-5 h-5" />
      <span>Back</span>
    </button>

    <!-- Dive Information Card -->
    <div
      class="backdrop-blur-sm rounded-xl p-6 border mb-6"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <div class="flex flex-col md:flex-row md:items-start justify-between gap-4 mb-4">
        <div class="flex items-center gap-3 flex-wrap">
          <h1 class="text-white text-2xl font-medium">{{ currentDive.name }}</h1>
          <span
            class="px-2.5 py-0.5 rounded-full text-xs font-medium"
            style="background-color: rgba(0, 212, 170, 0.2); color: #00D4AA; border: 1px solid rgba(0, 212, 170, 0.4)"
          >
            Completed
          </span>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            class="px-3 py-1.5 text-sm rounded-lg transition-all hover:opacity-90 flex items-center gap-1.5"
            style="background: linear-gradient(135deg, #FF9937 0%, #FF621D 100%); color: white"
          >
            <Download class="w-3.5 h-3.5" />
            Download All
          </button>
          <button
            class="px-3 py-1.5 text-sm rounded-lg transition-all hover:opacity-90 flex items-center gap-1.5"
            style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%); color: white"
          >
            <FileText class="w-3.5 h-3.5" />
            Open Log
          </button>
          <button
            @click="confirmDelete"
            class="px-3 py-1.5 text-sm rounded-lg transition-all hover:opacity-90 flex items-center gap-1.5"
            style="background-color: rgba(255, 71, 87, 0.15); color: #FF4757; border: 1px solid rgba(255, 71, 87, 0.3)"
          >
            <Trash2 class="w-3.5 h-3.5" />
            Delete Dive
          </button>
        </div>
      </div>

      <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <div class="flex items-center gap-2">
          <Calendar class="w-4 h-4 flex-shrink-0" style="color: #41B9C3" />
          <div>
            <p class="text-xs" style="color: #96EEF2">Date</p>
            <p class="text-white text-sm">{{ currentDive.date }}</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <Clock class="w-4 h-4 flex-shrink-0" style="color: #41B9C3" />
          <div>
            <p class="text-xs" style="color: #96EEF2">Duration</p>
            <p class="text-white text-sm">{{ currentDive.duration }}</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <Gauge class="w-4 h-4 flex-shrink-0" style="color: #41B9C3" />
          <div>
            <p class="text-xs" style="color: #96EEF2">Max Depth</p>
            <p class="text-white text-sm">{{ currentDive.maxDepth }}</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <MapPin class="w-4 h-4 flex-shrink-0" style="color: #41B9C3" />
          <div>
            <p class="text-xs" style="color: #96EEF2">Location</p>
            <p class="text-white text-sm">{{ currentDive.location }}</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <User class="w-4 h-4 flex-shrink-0" style="color: #41B9C3" />
          <div>
            <p class="text-xs" style="color: #96EEF2">Operator</p>
            <p class="text-white text-sm">{{ currentDive.operator ?? 'N/A' }}</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <Image class="w-4 h-4 flex-shrink-0" style="color: #41B9C3" />
          <div>
            <p class="text-xs" style="color: #96EEF2">Media</p>
            <p class="text-white text-sm">{{ imageCount }} img / {{ videoCount }} vid</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Media Gallery -->
    <div
      class="backdrop-blur-sm rounded-xl p-6 border"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <h2 class="text-white text-xl mb-4 flex items-center gap-2">
        <Image class="w-5 h-5" style="color: #96EEF2" />
        Media Gallery
      </h2>

      <div
        v-if="loading"
        class="flex items-center justify-center gap-2 py-16"
        style="color: #96EEF2"
      >
        <Loader2 class="w-6 h-6 animate-spin" />
        <span>Loading media…</span>
      </div>

      <div
        v-else-if="error"
        class="flex items-center gap-2 py-8 px-2 rounded-lg"
        style="background-color: rgba(221, 44, 29, 0.12); color: #FF6B6B"
      >
        <AlertCircle class="w-5 h-5 flex-shrink-0" />
        <span>{{ error }}</span>
      </div>

      <p
        v-else-if="mediaItems.length === 0"
        class="py-12 text-center text-sm"
        style="color: rgba(150, 238, 242, 0.7)"
      >
        No images or videos found for this dive.
      </p>

      <div
        v-else
        class="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 xl:grid-cols-8 gap-2"
      >
        <div
          v-for="(item, index) in mediaItems"
          :key="item.id"
          class="relative group cursor-pointer aspect-square rounded-lg overflow-hidden"
          style="background-color: rgba(14, 36, 70, 0.5)"
          @click="openLightbox(index)"
        >
          <video
            v-if="item.type === 'video'"
            :src="item.thumbnail"
            muted
            playsinline
            preload="metadata"
            class="w-full h-full object-cover transition-transform duration-300 group-hover:scale-110"
          />
          <img
            v-else
            :src="item.thumbnail"
            :alt="`Media ${item.id}`"
            class="w-full h-full object-cover transition-transform duration-300 group-hover:scale-110"
            loading="lazy"
          />
          <!-- Hover Overlay -->
          <div class="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-all duration-300 flex items-center justify-center">
            <ZoomIn class="w-6 h-6 text-white opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
          </div>
          <!-- Video Badge -->
          <div
            v-if="item.type === 'video'"
            class="absolute bottom-1 left-1 flex items-center gap-1 px-1.5 py-0.5 rounded text-xs"
            style="background-color: rgba(0, 0, 0, 0.7); color: white"
          >
            <Play class="w-3 h-3" />
            <span v-if="item.duration">{{ item.duration }}</span>
            <span v-else>Video</span>
          </div>
          <!-- Type Icon -->
          <div
            v-if="item.type === 'video'"
            class="absolute top-1 right-1 w-6 h-6 rounded-full flex items-center justify-center"
            style="background-color: rgba(168, 85, 247, 0.8)"
          >
            <Video class="w-3 h-3 text-white" />
          </div>
        </div>
      </div>
    </div>

    <!-- Lightbox Modal -->
    <Teleport to="body">
      <div
        v-if="lightboxItem"
        class="fixed inset-0 z-50 flex items-center justify-center"
        style="background-color: rgba(0, 0, 0, 0.9)"
        @click.self="closeLightbox"
        @keydown="handleKeydown"
      >
        <!-- Close Button -->
        <button
          @click="closeLightbox"
          class="absolute top-4 right-4 w-10 h-10 rounded-full flex items-center justify-center text-white transition-colors z-10"
          style="background-color: rgba(255, 255, 255, 0.1)"
        >
          &times;
        </button>

        <!-- Previous -->
        <button
          v-if="lightboxIndex !== null && lightboxIndex > 0"
          @click.stop="prevImage"
          class="absolute left-4 top-1/2 -translate-y-1/2 w-10 h-10 rounded-full flex items-center justify-center text-white transition-colors z-10"
          style="background-color: rgba(255, 255, 255, 0.1)"
        >
          <ArrowLeft class="w-5 h-5" />
        </button>

        <!-- Next -->
        <button
          v-if="lightboxIndex !== null && lightboxIndex < mediaItems.length - 1"
          @click.stop="nextImage"
          class="absolute right-4 top-1/2 -translate-y-1/2 w-10 h-10 rounded-full flex items-center justify-center text-white transition-colors rotate-180 z-10"
          style="background-color: rgba(255, 255, 255, 0.1)"
        >
          <ArrowLeft class="w-5 h-5" />
        </button>

        <!-- Image / video -->
        <div class="max-w-5xl max-h-[85vh] flex flex-col items-center">
          <video
            v-if="lightboxItem.type === 'video'"
            :src="lightboxItem.url"
            controls
            playsinline
            class="max-w-full max-h-[75vh] object-contain rounded-lg"
          />
          <img
            v-else
            :src="lightboxItem.url"
            :alt="`Media ${lightboxItem.id}`"
            class="max-w-full max-h-[75vh] object-contain rounded-lg"
          />
          <!-- Info Bar -->
          <div
            class="mt-3 px-4 py-2 rounded-lg flex items-center gap-6 text-sm"
            style="background-color: rgba(14, 36, 70, 0.8); border: 1px solid rgba(65, 185, 195, 0.3)"
          >
            <div class="flex items-center gap-1.5">
              <Clock class="w-3.5 h-3.5" style="color: #41B9C3" />
              <span style="color: #96EEF2">{{ lightboxItem.timestamp }}</span>
            </div>
            <div class="flex items-center gap-1.5">
              <Gauge class="w-3.5 h-3.5" style="color: #41B9C3" />
              <span style="color: #96EEF2">Depth: {{ lightboxItem.depth }}</span>
            </div>
            <span style="color: rgba(150, 238, 242, 0.5)">
              {{ (lightboxIndex ?? 0) + 1 }} / {{ mediaItems.length }}
            </span>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Delete Confirmation Modal -->
    <Teleport to="body">
      <div
        v-if="showDeleteModal"
        class="fixed inset-0 z-50 flex items-center justify-center p-4"
        style="background-color: rgba(0, 0, 0, 0.7)"
        @click.self="cancelDelete"
      >
        <div
          class="rounded-xl p-6 max-w-md w-full border"
          style="background-color: #0E2446; border-color: rgba(255, 71, 87, 0.4)"
        >
          <div class="flex items-center gap-3 mb-4">
            <div
              class="w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0"
              style="background-color: rgba(255, 71, 87, 0.2)"
            >
              <Trash2 class="w-5 h-5" style="color: #FF4757" />
            </div>
            <h3 class="text-white text-lg font-medium">Delete Dive</h3>
          </div>
          <p class="mb-2" style="color: #96EEF2">
            Are you sure you want to delete
            <span class="text-white font-medium">{{ currentDive.name }}</span>?
          </p>
          <p class="text-sm mb-6" style="color: rgba(150, 238, 242, 0.6)">
            This will permanently remove all media files and associated data. This action cannot be undone.
          </p>
          <div class="flex gap-3 justify-end">
            <button
              @click="cancelDelete"
              class="px-4 py-2 rounded-lg transition-all"
              style="background-color: rgba(14, 36, 70, 0.5); color: #96EEF2; border: 1px solid rgba(65, 185, 195, 0.3)"
            >
              Cancel
            </button>
            <button
              @click="executeDelete"
              class="px-4 py-2 rounded-lg transition-all hover:opacity-90 flex items-center gap-2"
              style="background-color: #FF4757; color: white"
            >
              <Trash2 class="w-4 h-4" />
              Delete Dive
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
