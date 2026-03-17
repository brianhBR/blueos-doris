<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { Wifi, WifiOff } from 'lucide-vue-next'
import { useAttitudeWs } from '../composables/useAttitudeWs'

const MODEL_PATH = '/models/VECTORED_6DOF.glb'

const { attitude, connected } = useAttitudeWs()

onMounted(() => {
  import('@google/model-viewer')
})

const orientationStr = computed(() => {
  if (!attitude.value) return '0deg 0deg 0deg'
  return `${attitude.value.pitch_deg}deg ${attitude.value.yaw_deg}deg ${attitude.value.roll_deg}deg`
})

function formatValue(value: number): string {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}`
}
</script>

<template>
  <div class="relative w-full h-full">
    <model-viewer
      :src="MODEL_PATH"
      :orientation="orientationStr"
      camera-controls
      disable-zoom
      interaction-prompt="none"
      camera-orbit="45deg 65deg 2.5m"
      min-camera-orbit="auto auto 1.5m"
      max-camera-orbit="auto auto 5m"
      shadow-intensity="0.3"
      exposure="1.2"
      interpolation-decay="50"
      style="width: 100%; height: 100%; --poster-color: transparent;"
    />

    <!-- Connection badge -->
    <div
      class="absolute top-2 right-2 flex items-center gap-1 px-2 py-1 rounded-full text-xs"
      :style="{
        backgroundColor: connected ? 'rgba(0, 212, 170, 0.2)' : 'rgba(255, 71, 87, 0.2)',
        color: connected ? '#00D4AA' : '#FF4757',
      }"
    >
      <Wifi v-if="connected" class="w-3 h-3" />
      <WifiOff v-else class="w-3 h-3" />
      {{ connected ? 'Live' : 'Off' }}
    </div>

    <!-- Attitude readout -->
    <div class="absolute bottom-2 left-2 right-2 flex justify-center gap-1.5">
      <div
        v-for="axis in [
          { label: 'R', value: attitude?.roll_deg ?? 0, color: '#00D4AA' },
          { label: 'P', value: attitude?.pitch_deg ?? 0, color: '#41B9C3' },
          { label: 'Y', value: attitude?.yaw_deg ?? 0, color: '#FF8C42' },
        ]"
        :key="axis.label"
        class="px-2 py-1 rounded text-center flex-1"
        style="background-color: rgba(14, 36, 70, 0.88); backdrop-filter: blur(8px)"
      >
        <div class="text-[10px] uppercase tracking-wider" :style="{ color: axis.color }">
          {{ axis.label }}
        </div>
        <div class="text-sm font-mono font-bold text-white leading-tight">
          {{ formatValue(axis.value) }}°
        </div>
      </div>
    </div>
  </div>
</template>
