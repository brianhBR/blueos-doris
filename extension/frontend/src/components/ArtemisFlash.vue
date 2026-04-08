<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { Cpu, RefreshCw, Upload, Zap } from 'lucide-vue-next'
import { useArtemis, useArtemisFlash } from '../composables/useApi'

const { ports, loading: portsLoading, error: apiError, fetchPorts, uploadFirmware } = useArtemis()
const { flashing, progress, result, error: flashError, startFlash, stopPolling } = useArtemisFlash()

const selectedPort = ref('')
const selectedFile = ref<File | null>(null)
const baudOptions = [57600, 115200, 230400, 460800, 921600]
const baud = ref(460800)
const timeout = ref(0.5)
const uploading = ref(false)
const consoleEl = ref<HTMLDivElement | null>(null)

const canFlash = computed(() =>
  selectedPort.value && selectedFile.value && !flashing.value && !uploading.value
)

const displayError = computed(() => apiError.value || flashError.value)

onMounted(() => {
  fetchPorts()
})

onUnmounted(() => {
  stopPolling()
})

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  selectedFile.value = input.files?.[0] ?? null
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

async function handleFlash() {
  if (!selectedFile.value || !selectedPort.value) return

  uploading.value = true
  const uploadResult = await uploadFirmware(selectedFile.value)
  uploading.value = false

  if (!uploadResult) return

  startFlash({
    port: selectedPort.value,
    firmware_path: uploadResult.path,
    baud: baud.value,
    timeout: timeout.value,
  })
}

function lineClass(line: string): string {
  const lower = line.toLowerCase()
  if (lower.includes('success')) return 'text-emerald-400'
  if (lower.includes('failed') || lower.includes('error') || lower.includes('timeout')) return 'text-red-400'
  return 'text-gray-300'
}

watch(progress, async () => {
  await nextTick()
  if (consoleEl.value) {
    consoleEl.value.scrollTop = consoleEl.value.scrollHeight
  }
})
</script>

<template>
  <div class="max-w-3xl mx-auto px-4 py-8 space-y-6">
    <!-- Header -->
    <div class="flex items-center gap-3 mb-2">
      <Cpu class="w-7 h-7" style="color: #00D4AA" />
      <h2 class="text-2xl text-white" style="font-weight: 700; font-family: 'Montserrat', sans-serif">
        Artemis Firmware Flash
      </h2>
    </div>
    <p class="text-sm" style="color: #96EEF2">
      Upload a firmware binary and flash it to an Artemis Apollo3 board via the SparkFun Variable Loader.
    </p>

    <!-- Card -->
    <div class="rounded-xl border p-6 space-y-5" style="background: rgba(255,255,255,0.05); border-color: rgba(65,185,195,0.2)">

      <!-- Serial port -->
      <div class="space-y-1.5">
        <label class="block text-sm font-medium" style="color: #96EEF2">Serial Port</label>
        <div class="flex gap-2">
          <select
            v-model="selectedPort"
            class="flex-1 rounded-lg px-3 py-2 text-sm text-white border focus:outline-none focus:ring-1"
            style="background: rgba(14,36,70,0.6); border-color: rgba(65,185,195,0.3)"
            :disabled="flashing"
          >
            <option value="" disabled>Select a port...</option>
            <option v-for="port in ports" :key="port.device" :value="port.device">
              {{ port.device }} — {{ port.description }}
            </option>
          </select>
          <button
            @click="fetchPorts"
            :disabled="portsLoading || flashing"
            class="px-3 py-2 rounded-lg border transition-colors"
            style="border-color: rgba(65,185,195,0.3); color: #96EEF2"
            :style="{ opacity: portsLoading || flashing ? 0.5 : 1 }"
          >
            <RefreshCw class="w-4 h-4" :class="{ 'animate-spin': portsLoading }" />
          </button>
        </div>
      </div>

      <!-- Firmware file -->
      <div class="space-y-1.5">
        <label class="block text-sm font-medium" style="color: #96EEF2">Firmware File (.bin)</label>
        <label
          class="flex items-center gap-3 rounded-lg border border-dashed px-4 py-4 cursor-pointer transition-colors"
          style="border-color: rgba(65,185,195,0.3); color: #96EEF2"
          :style="{ opacity: flashing ? 0.5 : 1 }"
        >
          <Upload class="w-5 h-5 flex-shrink-0" />
          <div class="flex-1 min-w-0">
            <span v-if="selectedFile" class="text-white text-sm truncate block">
              {{ selectedFile.name }}
              <span class="ml-2 text-xs" style="color: #96EEF2">{{ formatSize(selectedFile.size) }}</span>
            </span>
            <span v-else class="text-sm">Click to select a .bin firmware file</span>
          </div>
          <input
            type="file"
            accept=".bin"
            class="hidden"
            :disabled="flashing"
            @change="onFileChange"
          >
        </label>
      </div>

      <!-- Settings -->
      <div class="grid grid-cols-2 gap-4">
        <div class="space-y-1.5">
          <label class="block text-sm font-medium" style="color: #96EEF2">Baud Rate</label>
          <select
            v-model.number="baud"
            :disabled="flashing"
            class="w-full rounded-lg px-3 py-2 text-sm text-white border focus:outline-none focus:ring-1"
            style="background: rgba(14,36,70,0.6); border-color: rgba(65,185,195,0.3)"
          >
            <option v-for="b in baudOptions" :key="b" :value="b">{{ b.toLocaleString() }}</option>
          </select>
        </div>
        <div class="space-y-1.5">
          <label class="block text-sm font-medium" style="color: #96EEF2">Timeout (s)</label>
          <input
            v-model.number="timeout"
            type="number"
            step="0.1"
            :disabled="flashing"
            class="w-full rounded-lg px-3 py-2 text-sm text-white border focus:outline-none focus:ring-1"
            style="background: rgba(14,36,70,0.6); border-color: rgba(65,185,195,0.3)"
          >
        </div>
      </div>

      <!-- Flash button -->
      <button
        @click="handleFlash"
        :disabled="!canFlash"
        class="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg text-white font-semibold transition-all"
        :style="{
          backgroundColor: canFlash ? '#00D4AA' : 'rgba(0,212,170,0.3)',
          cursor: canFlash ? 'pointer' : 'not-allowed',
        }"
      >
        <Zap v-if="!flashing && !uploading" class="w-5 h-5" />
        <svg v-else class="w-5 h-5 animate-spin" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-dasharray="31.4 31.4" />
        </svg>
        <span v-if="uploading">Uploading firmware...</span>
        <span v-else-if="flashing">Flashing...</span>
        <span v-else>Flash Firmware</span>
      </button>
    </div>

    <!-- Error banner -->
    <div
      v-if="displayError"
      class="rounded-lg px-4 py-3 text-sm font-medium"
      style="background: rgba(255,71,87,0.15); color: #FF4757; border: 1px solid rgba(255,71,87,0.3)"
    >
      {{ displayError }}
    </div>

    <!-- Output console -->
    <div
      v-if="progress.length > 0"
      class="rounded-xl border overflow-hidden"
      style="border-color: rgba(65,185,195,0.2)"
    >
      <div class="px-4 py-2 text-xs font-semibold tracking-wide" style="background: rgba(14,36,70,0.8); color: #96EEF2">
        OUTPUT
      </div>
      <div
        ref="consoleEl"
        class="px-4 py-3 overflow-y-auto text-sm leading-relaxed"
        style="background: rgba(0,0,0,0.4); max-height: 320px; font-family: 'JetBrains Mono', 'Fira Code', monospace"
      >
        <div v-for="(line, i) in progress" :key="i" :class="lineClass(line)">
          {{ line }}
        </div>
      </div>
    </div>

    <!-- Result banner -->
    <div
      v-if="result"
      class="rounded-lg px-4 py-3 text-sm font-semibold"
      :style="{
        background: result.success ? 'rgba(0,212,170,0.15)' : 'rgba(255,71,87,0.15)',
        color: result.success ? '#00D4AA' : '#FF4757',
        border: result.success ? '1px solid rgba(0,212,170,0.3)' : '1px solid rgba(255,71,87,0.3)',
      }"
    >
      {{ result.success ? 'Firmware flashed successfully!' : 'Firmware flash failed.' }}
    </div>
  </div>
</template>
