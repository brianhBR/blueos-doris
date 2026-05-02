<script setup lang="ts">
import { computed } from 'vue'
import { Download, Loader2, CheckCircle2, AlertTriangle, X } from 'lucide-vue-next'
import { useDownloadQueue, type DownloadJob } from '../composables/useDownloads'

const { jobs, dismissJob, dismissFinishedJobs } = useDownloadQueue()

// `jobs` is a reactive readonly array (not a ref), so we read it directly.
const visibleJobs = computed<readonly DownloadJob[]>(() => jobs as readonly DownloadJob[])

const hasFinished = computed(() => visibleJobs.value.some(j => j.phase === 'done' || j.phase === 'error'))

function fmtBytes(b: number): string {
  if (!b || !Number.isFinite(b)) return '—'
  if (b >= 1e9) return `${(b / 1e9).toFixed(2)} GB`
  if (b >= 1e6) return `${(b / 1e6).toFixed(0)} MB`
  if (b >= 1e3) return `${(b / 1e3).toFixed(0)} KB`
  return `${b} B`
}

function phaseLabel(j: DownloadJob): string {
  if (j.phase === 'preparing') return 'Preparing…'
  if (j.phase === 'starting') return 'Starting download…'
  if (j.phase === 'streaming') return 'Download started — see your browser tray'
  if (j.phase === 'done') return 'Download started'
  if (j.phase === 'error') return 'Download failed'
  return ''
}

function elapsed(j: DownloadJob): string {
  const s = Math.max(0, Math.floor((Date.now() - j.startedAt) / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}m ${r}s`
}
</script>

<template>
  <div
    v-if="visibleJobs.length > 0"
    class="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm w-[calc(100vw-2rem)] sm:w-96"
    style="font-family: Montserrat, sans-serif"
  >
    <div
      v-if="hasFinished && visibleJobs.length > 1"
      class="flex justify-end"
    >
      <button
        class="text-xs px-2 py-1 rounded transition-colors hover:bg-white/10"
        style="color: #96EEF2"
        @click="dismissFinishedJobs"
      >
        Clear finished
      </button>
    </div>

    <div
      v-for="job in visibleJobs"
      :key="job.id"
      class="rounded-lg p-3 shadow-lg border flex items-start gap-3"
      :style="{
        backgroundColor: 'rgba(14, 36, 70, 0.95)',
        borderColor: job.phase === 'error'
          ? 'rgba(221, 44, 29, 0.6)'
          : (job.phase === 'done'
              ? 'rgba(65, 185, 195, 0.6)'
              : 'rgba(65, 185, 195, 0.4)'),
      }"
    >
      <div class="flex-shrink-0 mt-0.5">
        <Loader2
          v-if="job.phase === 'preparing' || job.phase === 'starting'"
          class="w-5 h-5 animate-spin"
          style="color: #41B9C3"
        />
        <Download
          v-else-if="job.phase === 'streaming'"
          class="w-5 h-5"
          style="color: #41B9C3"
        />
        <CheckCircle2
          v-else-if="job.phase === 'done'"
          class="w-5 h-5"
          style="color: #41B9C3"
        />
        <AlertTriangle
          v-else-if="job.phase === 'error'"
          class="w-5 h-5"
          style="color: #DD2C1D"
        />
      </div>

      <div class="flex-1 min-w-0">
        <div class="flex items-baseline gap-2 justify-between">
          <p
            class="text-sm font-medium truncate"
            style="color: #E0F7FA"
            :title="job.fileName"
          >
            {{ job.fileName }}
          </p>
          <span
            v-if="job.total > 1"
            class="text-xs flex-shrink-0"
            style="color: #96EEF2"
          >
            {{ job.index }} of {{ job.total }}
          </span>
        </div>
        <p
          class="text-xs mt-0.5"
          :style="{ color: job.phase === 'error' ? '#FF6B6B' : '#96EEF2' }"
        >
          {{ phaseLabel(job) }}
        </p>
        <p class="text-xs mt-1" style="color: rgba(150, 238, 242, 0.7)">
          <span v-if="job.sizeBytes > 0">{{ fmtBytes(job.sizeBytes) }}</span>
          <span v-if="job.sizeBytes > 0"> · </span>
          <span>Elapsed {{ elapsed(job) }}</span>
        </p>
        <p
          v-if="job.phase === 'error' && job.error"
          class="text-xs mt-1"
          style="color: #FF6B6B"
        >
          {{ job.error }}
        </p>
      </div>

      <button
        class="flex-shrink-0 p-1 rounded transition-colors hover:bg-white/10"
        :title="job.phase === 'streaming' || job.phase === 'done' ? 'Dismiss' : 'Cancel toast'"
        @click="dismissJob(job.id)"
      >
        <X class="w-4 h-4" style="color: #96EEF2" />
      </button>
    </div>
  </div>
</template>
