<script setup lang="ts">
import { computed } from 'vue'
import { Home, Wifi, Compass, Gauge, Database, Bell, HelpCircle, MapPin } from 'lucide-vue-next'
import type { Screen } from '../types'

interface Props {
  currentScreen: Screen
  isConnected: boolean
  notificationCount?: number
}

const props = withDefaults(defineProps<Props>(), {
  notificationCount: 3
})

const emit = defineEmits<{
  navigate: [screen: Screen]
}>()

const navItems = computed(() => [
  { id: 'home' as Screen, icon: Home, label: 'Status' },
  { id: 'location' as Screen, icon: MapPin, label: 'Location' },
  { id: 'missions' as Screen, icon: Compass, label: 'Missions' },
  { id: 'sensors' as Screen, icon: Gauge, label: 'Sensors' },
  { id: 'media' as Screen, icon: Database, label: 'Media' },
  { id: 'network' as Screen, icon: Wifi, label: 'Network' },
  { id: 'notifications' as Screen, icon: Bell, label: 'Notifications', badge: props.notificationCount },
])
</script>

<template>
  <!-- Desktop Navigation -->
  <nav 
    class="hidden md:flex items-center justify-between px-6 py-4 border-b"
    style="background-color: rgba(14, 36, 70, 0.8); backdrop-filter: blur(8px); border-color: rgba(65, 185, 195, 0.2)"
  >
    <button 
      @click="emit('navigate', 'home')"
      class="flex items-center gap-6 hover:opacity-80 transition-opacity mr-12"
    >
      <h1 class="text-white font-bold text-xl">DORIS</h1>
    </button>
    
    <div class="flex items-center gap-2">
      <button
        v-for="item in navItems"
        :key="item.id"
        @click="emit('navigate', item.id)"
        class="flex items-center gap-2 px-4 py-2 rounded-lg transition-all"
        :style="currentScreen === item.id 
          ? { backgroundColor: '#41B9C3', color: '#FFFFFF' }
          : { color: '#96EEF2' }"
        :class="currentScreen !== item.id && 'hover:bg-slate-800'"
      >
        <component :is="item.icon" class="w-4 h-4" />
        <span v-if="item.id !== 'notifications'" class="text-sm">{{ item.label }}</span>
        <span 
          v-if="item.badge && item.badge > 0"
          class="ml-1 text-xs px-1.5 py-0.5 rounded-full text-center"
          style="background-color: #DD2C1D; color: white; min-width: 20px"
        >
          {{ item.badge }}
        </span>
      </button>
    </div>

    <div class="flex items-center gap-4">
      <!-- Help - Display Only -->
      <div class="flex items-center gap-2 px-3 py-2" style="color: #96EEF2; opacity: 0.6">
        <HelpCircle class="w-4 h-4" />
        <span class="text-sm">Help</span>
      </div>
      
      <!-- Connection Status -->
      <div class="flex items-center gap-2">
        <div 
          class="w-2 h-2 rounded-full"
          :style="{ backgroundColor: isConnected ? '#FCD869' : '#DD2C1D' }"
        ></div>
        <span class="text-sm" style="color: #96EEF2">
          {{ isConnected ? 'Connected' : 'Disconnected' }}
        </span>
      </div>
    </div>
  </nav>

  <!-- Mobile Navigation -->
  <nav 
    class="md:hidden fixed bottom-0 left-0 right-0 border-t z-50"
    style="background-color: rgba(14, 36, 70, 0.95); backdrop-filter: blur(8px); border-color: rgba(65, 185, 195, 0.2)"
  >
    <div class="flex items-center justify-around px-2 py-2">
      <button
        v-for="item in navItems"
        :key="item.id"
        @click="emit('navigate', item.id)"
        class="relative flex flex-col items-center gap-1 px-3 py-2 rounded-lg transition-all"
        :style="currentScreen === item.id 
          ? { backgroundColor: '#41B9C3', color: '#FFFFFF' }
          : { color: '#96EEF2' }"
      >
        <component :is="item.icon" class="w-5 h-5" />
        <span v-if="item.id !== 'notifications'" class="text-xs">{{ item.label }}</span>
        <span 
          v-if="item.badge && item.badge > 0"
          class="absolute top-1 right-1 text-xs px-1.5 py-0.5 rounded-full text-center"
          style="background-color: #DD2C1D; color: white; min-width: 18px; font-size: 10px"
        >
          {{ item.badge }}
        </span>
      </button>
    </div>
  </nav>
</template>

