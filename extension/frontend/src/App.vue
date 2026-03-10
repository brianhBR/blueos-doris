<script setup lang="ts">
import { ref } from 'vue'
import type { Screen } from './types'
import Navigation from './components/Navigation.vue'
import Footer from './components/Footer.vue'
import HomeScreen from './components/HomeScreen.vue'
import NetworkSetup from './components/NetworkSetup.vue'
import MissionProgramming from './components/MissionProgramming.vue'
import SensorConfiguration from './components/SensorConfiguration.vue'
import MediaManager from './components/MediaManager.vue'
import HelpTutorials from './components/HelpTutorials.vue'
import Notifications from './components/Notifications.vue'
import Location from './components/Location.vue'

const currentScreen = ref<Screen>('home')
const isConnected = ref(false)

const navigateTo = (screen: Screen) => {
  currentScreen.value = screen
}

const setConnected = (connected: boolean) => {
  isConnected.value = connected
}
</script>

<template>
  <div 
    class="min-h-screen flex flex-col"
    style="background: linear-gradient(135deg, #0E2446 0%, #0E2446 60%, #004D64 100%)"
  >
    <Navigation 
      :current-screen="currentScreen"
      :is-connected="isConnected"
      @navigate="navigateTo"
    />
    
    <main class="pb-20 md:pb-0 flex-grow">
      <HomeScreen 
        v-if="currentScreen === 'home'"
        :is-connected="isConnected"
        @navigate="navigateTo"
      />
      <MissionProgramming v-else-if="currentScreen === 'missions'" />
      <SensorConfiguration v-else-if="currentScreen === 'sensors'" />
      <MediaManager v-else-if="currentScreen === 'media'" />
      <NetworkSetup 
        v-else-if="currentScreen === 'network'"
        @connect="setConnected"
      />
      <Notifications 
        v-else-if="currentScreen === 'notifications'"
        @navigate="navigateTo"
      />
      <HelpTutorials v-else-if="currentScreen === 'help'" />
      <Location v-else-if="currentScreen === 'location'" />
    </main>
    
    <Footer />
  </div>
</template>

