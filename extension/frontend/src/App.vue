<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { HardDrive, Loader2, AlertTriangle, Clock, CircleCheck, CircleX, Usb, X } from 'lucide-vue-next'
import type { Screen, DiveData } from './types'
import { useDiveControl, useDiveProcessing, useNotifications, useStorageMigration, useArmingStatus } from './composables/useApi'
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
import AllMissions from './components/AllMissions.vue'
import ViewMediaScreen from './components/ViewMediaScreen.vue'
import ArtemisFlash from './components/ArtemisFlash.vue'
import DownloadToast from './components/DownloadToast.vue'

const currentScreen = ref<Screen>('home')
const previousScreen = ref<Screen>('media')
const isConnected = ref(false)
const targetSensor = ref<string | null>(null)

const { status: diveStatus, mission: diveMission, fetchDiveStatus, fetchDiveMission } = useDiveControl()
const isDiveActive = computed(() => diveStatus.value?.active === true)
const activeConfigName = computed(() => diveMission.value?.configuration_name?.trim() || '')

const { unreadCount: notificationCount, fetchUnreadCount } = useNotifications()
const { status: migrationStatus, isActive: migrationActive, isError: migrationError, fetchMigrationStatus } = useStorageMigration()

const { status: armingStatus, fetchArmingStatus } = useArmingStatus()
const showArmingBanner = computed(() => armingStatus.value?.waiting_to_arm === true)
// Strip the redundant "PreArm:"/"Arm:" prefix since the banner heading
// already says pre-arm checks are failing.
const armingReasons = computed(() =>
  (armingStatus.value?.reasons ?? []).map(r => r.text.replace(/^(PreArm:|Arm:)\s*/i, '').trim())
)

// Post-dive processing runs for minutes; the bar keeps it visible on every
// screen and stays up after it finishes until the operator dismisses it.
const {
  session: processingSession,
  isProcessing: processingActive,
  currentStep: processingStep,
  finishedSteps: processingFinishedSteps,
  totalSteps: processingTotalSteps,
  progressPercent: processingPercent,
  dismissed: processingDismissed,
  dismissProcessing,
} = useDiveProcessing()

const showProcessingBar = computed(
  () => processingSession.value !== null && !processingDismissed.value,
)

const processingStepNumber = computed(() =>
  Math.min(processingFinishedSteps.value + 1, Math.max(processingTotalSteps.value, 1)),
)

const processingBarStyle = computed(() => {
  if (processingActive.value) {
    return {
      background: 'linear-gradient(90deg, #0E2446 0%, #004D64 100%)',
      borderBottom: '1px solid rgba(65, 185, 195, 0.4)',
    }
  }
  if (processingSession.value?.success) {
    return {
      backgroundColor: 'rgba(0, 212, 170, 0.15)',
      borderBottom: '1px solid rgba(0, 212, 170, 0.5)',
    }
  }
  return {
    backgroundColor: 'rgba(221, 44, 29, 0.15)',
    borderBottom: '1px solid rgba(221, 44, 29, 0.5)',
  }
})

const clockSyncFailed = ref(false)
const clockSyncMessage = ref('')

async function syncVehicleClock() {
  try {
    const statusRes = await fetch('/api/v1/system/time')
    if (!statusRes.ok) return
    const status = await statusRes.json() as {
      synced: boolean
      clock_sane: boolean
      source: string | null
      last_drift_seconds: number | null
    }

    if (status.synced && status.clock_sane) {
      clockSyncFailed.value = false
      return
    }

    if (status.clock_sane) {
      clockSyncFailed.value = false
      return
    }

    const res = await fetch('/api/v1/system/time', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ utc: new Date().toISOString() }),
    })
    if (!res.ok) {
      clockSyncFailed.value = true
      clockSyncMessage.value = 'Vehicle clock is wrong — could not sync'
      return
    }
    const data = await res.json() as {
      synced: boolean
      drift_seconds: number
      clock_sane: boolean
      reason?: string
    }
    if (data.synced) {
      console.info(`Vehicle clock synced from browser (was off by ${data.drift_seconds}s)`)
      clockSyncFailed.value = false
    } else if (!data.clock_sane) {
      clockSyncFailed.value = true
      clockSyncMessage.value =
        'Vehicle clock is wrong — waiting for Artemis GPS time sync'
    } else {
      clockSyncFailed.value = false
    }
  } catch {
    // backend not reachable yet
  }
}

let divePolling: number | undefined
let notifPolling: number | undefined
let migrationPolling: number | undefined
let clockPolling: number | undefined
onMounted(() => {
  syncVehicleClock()
  fetchDiveStatus()
  fetchDiveMission()
  fetchUnreadCount()
  fetchMigrationStatus()
  fetchArmingStatus()
  divePolling = setInterval(() => { fetchDiveStatus(); fetchDiveMission(); fetchArmingStatus() }, 5000) as unknown as number
  notifPolling = setInterval(fetchUnreadCount, 15000) as unknown as number
  migrationPolling = setInterval(fetchMigrationStatus, 3000) as unknown as number
  clockPolling = setInterval(syncVehicleClock, 60000) as unknown as number
})
onUnmounted(() => {
  if (divePolling) clearInterval(divePolling)
  if (notifPolling) clearInterval(notifPolling)
  if (migrationPolling) clearInterval(migrationPolling)
  if (clockPolling) clearInterval(clockPolling)
})
const selectedDiveData = ref<DiveData | null>(null)
const releaseWeightBy = ref<'datetime' | 'elapsed'>('elapsed')
const selectedConfigFromDashboard = ref('')

const handleNavigate = (screen: Screen, sensorName?: string) => {
  previousScreen.value = currentScreen.value
  currentScreen.value = screen
  if (screen === 'sensors' && sensorName) {
    targetSensor.value = sensorName
  } else {
    targetSensor.value = null
  }
  if (screen !== 'viewmedia') {
    selectedDiveData.value = null
  }
}

const handleSetCurrentScreen = (screen: Screen, diveData?: DiveData) => {
  previousScreen.value = currentScreen.value
  currentScreen.value = screen
  if (screen === 'viewmedia' && diveData) {
    selectedDiveData.value = diveData
  } else if (screen !== 'viewmedia') {
    selectedDiveData.value = null
  }
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
    <div
      v-if="clockSyncFailed"
      class="w-full py-2 px-4 flex items-center justify-center gap-2"
      style="background-color: rgba(255, 165, 0, 0.2); border-bottom: 1px solid rgba(255, 165, 0, 0.5); font-family: Montserrat, sans-serif"
    >
      <Clock class="w-4 h-4" style="color: #FFA500" />
      <span class="text-sm" style="color: #FFD180">
        {{ clockSyncMessage || 'Vehicle clock is not synced — dive timestamps may be incorrect' }}
      </span>
    </div>

    <div
      v-if="isDiveActive"
      class="w-full py-3 px-4 text-white text-center font-semibold"
      style="background-color: #DD2C1D; font-family: Montserrat, sans-serif"
    >
      Active Dive<span v-if="activeConfigName"> — {{ activeConfigName }}</span>
    </div>

    <div
      v-if="showArmingBanner"
      class="w-full py-2.5 px-4 flex flex-col items-center gap-1"
      style="background-color: rgba(255, 153, 55, 0.18); border-bottom: 1px solid rgba(255, 153, 55, 0.55); font-family: Montserrat, sans-serif"
    >
      <div class="flex items-center gap-2">
        <AlertTriangle class="w-4 h-4 flex-shrink-0" style="color: #FF9937" />
        <span class="text-sm font-semibold" style="color: #FFD180">Waiting to arm — pre-arm checks failing</span>
      </div>
      <ul class="text-xs text-center" style="color: #FFD180; opacity: 0.95">
        <li v-for="(reason, idx) in armingReasons" :key="idx">{{ reason }}</li>
      </ul>
    </div>

    <div
      v-if="migrationActive"
      class="w-full py-3 px-4 flex items-center justify-center gap-3"
      style="background: linear-gradient(90deg, #0E2446 0%, #004D64 100%); border-bottom: 1px solid rgba(65, 185, 195, 0.4); font-family: Montserrat, sans-serif"
    >
      <Loader2 class="w-5 h-5 animate-spin" style="color: #96EEF2" />
      <span class="text-white text-sm font-medium">
        <HardDrive class="w-4 h-4 inline-block mr-1" style="color: #96EEF2" />
        {{ migrationStatus?.message || 'Setting up external storage…' }}
      </span>
    </div>

    <div
      v-else-if="migrationError"
      class="w-full py-3 px-4 flex items-center justify-center gap-3"
      style="background-color: rgba(221, 44, 29, 0.15); border-bottom: 1px solid rgba(221, 44, 29, 0.5); font-family: Montserrat, sans-serif"
    >
      <AlertTriangle class="w-5 h-5" style="color: #DD2C1D" />
      <span class="text-sm" style="color: #FF6B6B">
        External storage setup failed: {{ migrationStatus?.error }}
      </span>
    </div>

    <div
      v-if="showProcessingBar"
      class="w-full py-2.5 px-4 flex flex-col items-center gap-1.5"
      :style="processingBarStyle"
      style="font-family: Montserrat, sans-serif"
    >
      <div class="w-full flex items-center justify-center gap-3">
        <Loader2 v-if="processingActive" class="w-5 h-5 flex-shrink-0 animate-spin" style="color: #96EEF2" />
        <CircleCheck
          v-else-if="processingSession?.success"
          class="w-5 h-5 flex-shrink-0"
          style="color: #00D4AA"
        />
        <CircleX v-else class="w-5 h-5 flex-shrink-0" style="color: #DD2C1D" />

        <span v-if="processingActive" class="text-white text-sm font-medium">
          Processing {{ processingSession?.dive_id }} —
          {{ processingStep?.label || 'Getting started…' }}
          <span class="opacity-80">
            (step {{ processingStepNumber }} of {{ processingTotalSteps }})
          </span>
        </span>
        <span v-else-if="processingSession?.success" class="text-sm font-medium" style="color: #00D4AA">
          Finished processing {{ processingSession?.dive_id }}.
          <span v-if="processingSession?.safe_to_remove_usb" class="inline-flex items-center gap-1 ml-1">
            <Usb class="w-4 h-4" />
            The USB stick is safe to remove.
          </span>
        </span>
        <span v-else class="text-sm font-medium" style="color: #FF6B6B">
          Processing {{ processingSession?.dive_id }} failed:
          {{ processingSession?.error || 'unknown error' }}
        </span>

        <button
          v-if="!processingActive"
          type="button"
          title="Dismiss"
          class="ml-2 p-1 rounded transition-opacity hover:opacity-70"
          @click="dismissProcessing"
        >
          <X class="w-4 h-4" style="color: #96EEF2" />
        </button>
      </div>

      <div
        v-if="processingActive"
        class="w-full max-w-xl h-1.5 rounded-full overflow-hidden"
        style="background-color: rgba(255, 255, 255, 0.15)"
      >
        <div
          class="h-full rounded-full transition-all"
          :style="{ width: `${processingPercent}%`, backgroundColor: '#00D4AA' }"
        />
      </div>
    </div>

    <Navigation
      :current-screen="currentScreen"
      :notification-count="notificationCount"
      @navigate="handleNavigate"
    />

    <main class="pb-0 flex-grow">
      <HomeScreen
        v-if="currentScreen === 'home'"
        :is-connected="isConnected"
        @navigate="handleNavigate"
        v-model:release-weight-by="releaseWeightBy"
        @configuration-select="selectedConfigFromDashboard = $event"
      />
      <MissionProgramming
        v-else-if="currentScreen === 'dives'"
        @navigate="handleSetCurrentScreen"
        v-model:release-weight-by="releaseWeightBy"
        :initial-configuration="selectedConfigFromDashboard"
      />
      <AllMissions
        v-else-if="currentScreen === 'alldives'"
        @navigate="handleSetCurrentScreen"
      />
      <SensorConfiguration
        v-else-if="currentScreen === 'sensors'"
        @navigate="handleSetCurrentScreen"
        :target-sensor="targetSensor"
      />
      <MediaManager
        v-else-if="currentScreen === 'media'"
        @navigate="handleSetCurrentScreen"
      />
      <NetworkSetup
        v-else-if="currentScreen === 'network'"
        @connect="setConnected"
      />
      <Notifications
        v-else-if="currentScreen === 'notifications'"
        @navigate="handleSetCurrentScreen"
      />
      <HelpTutorials v-else-if="currentScreen === 'help'" />
      <ArtemisFlash v-else-if="currentScreen === 'artemis'" />
      <Location v-else-if="currentScreen === 'location'" />
      <ViewMediaScreen
        v-else-if="currentScreen === 'viewmedia'"
        @navigate="handleSetCurrentScreen"
        :previous-screen="previousScreen"
        :dive-data="selectedDiveData"
      />
    </main>

    <Footer />

    <DownloadToast />
  </div>
</template>
