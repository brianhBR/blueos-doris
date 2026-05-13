<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { Wifi, Lock, RefreshCw, Signal, AlertCircle, CheckCircle, Smartphone, ArrowLeftRight } from 'lucide-vue-next'
import { useWifiNetworks } from '../composables/useApi'

const emit = defineEmits<{
  connect: [connected: boolean]
}>()

const {
  networks: apiNetworks,
  connectionStatus: apiConnectionStatus,
  serialNumber: apiSerialNumber,
  hotspotSsid: apiHotspotSsid,
  wlanState,
  scanning,
  switching,
  fetchNetworks,
  fetchWlanState,
  scanNetworks,
  switchToStaMode,
  switchToApMode,
} = useWifiNetworks()

const dorisMACAddress = computed(() => apiConnectionStatus.value?.mac_address ?? '—')
const dorisHotspotName = computed(() => apiHotspotSsid.value ?? 'DORIS')

const showAdvanced = ref(true)
const selectedNetwork = ref<DisplayNetwork | null>(null)
const password = ref('')
const manualSSID = ref('')
const manualPassword = ref('')

// AP/STA switch UX state
const pendingSwitch = ref<{ ssid: string; password: string } | null>(null)
const showSwitchConfirm = ref(false)
const dismissedAttemptTimestamp = ref<string | null>(null)

interface DisplayNetwork {
  ssid: string
  signal: number
  frequency: string
  security: string
  saved: boolean
  connected: boolean
}

const networks = computed<DisplayNetwork[]>(() => {
  if (apiNetworks.value.length > 0) {
    return apiNetworks.value
      // Don't list our own AP as a join target — the user can't STA into
      // their own hotspot, and showing it just invites confusion.
      .filter(n => n.ssid !== dorisHotspotName.value)
      .map((n) => ({
        ssid: n.ssid,
        signal: n.signal_strength,
        frequency: n.frequency,
        security: n.security,
        saved: n.is_saved,
        connected: n.is_connected,
      }))
  }
  return []
})

let pollInterval: number | undefined
let stateInterval: number | undefined

onMounted(() => {
  fetchNetworks()
  fetchWlanState()
  if (apiConnectionStatus.value?.is_connected) {
    emit('connect', true)
  }
  pollInterval = setInterval(fetchNetworks, 10000) as unknown as number
  // Faster cadence for WLAN state so the user gets prompt UI feedback
  // while a switch is in flight, and so the post-failure banner appears
  // quickly when they reconnect to the AP.
  stateInterval = setInterval(fetchWlanState, 3000) as unknown as number
})

onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval)
  if (stateInterval) clearInterval(stateInterval)
})

const isScanning = computed(() => scanning.value)

const currentMode = computed(() => wlanState.value?.mode ?? 'ap')
const isApMode = computed(() => currentMode.value === 'ap')
const isStaPending = computed(() => currentMode.value === 'sta_pending')
const isStaConnected = computed(() => currentMode.value === 'sta_connected')

// Show the failure banner only when:
//   - we're back on the AP (sta_pending wouldn't be reachable anyway)
//   - the most recent attempt was a failure
//   - the user hasn't already dismissed *this* failure
const failureBanner = computed(() => {
  const attempt = wlanState.value?.last_attempt
  if (!attempt || attempt.status !== 'failed') return null
  if (!isApMode.value) return null
  if (dismissedAttemptTimestamp.value === attempt.timestamp) return null
  return attempt
})

const handleScan = async () => {
  await scanNetworks()
}

const requestSwitch = (ssid: string, pwd: string) => {
  pendingSwitch.value = { ssid, password: pwd }
  showSwitchConfirm.value = true
}

const cancelSwitch = () => {
  pendingSwitch.value = null
  showSwitchConfirm.value = false
}

const confirmSwitch = async () => {
  if (!pendingSwitch.value) return
  const { ssid, password: pwd } = pendingSwitch.value
  showSwitchConfirm.value = false
  await switchToStaMode(ssid, pwd)
  // Don't clear pendingSwitch yet — we want to remember what SSID we
  // tried so the in-flight banner can show it. The poll loop will
  // refresh wlanState; the AP-side connection will die any moment now.
  password.value = ''
  manualPassword.value = ''
}

const handleConnectFromList = () => {
  if (!selectedNetwork.value) return
  const ssid = selectedNetwork.value.ssid
  const pwd = selectedNetwork.value.saved ? '' : password.value
  requestSwitch(ssid, pwd)
}

const handleManualConnect = () => {
  if (!manualSSID.value) return
  requestSwitch(manualSSID.value, manualPassword.value)
}

const handleRestoreHotspot = async () => {
  await switchToApMode()
}

const dismissFailureBanner = () => {
  if (failureBanner.value) {
    dismissedAttemptTimestamp.value = failureBanner.value.timestamp
  }
}

const getSignalColor = (signal: number) => {
  if (signal > 70) return '#FCD869'
  if (signal > 40) return '#FF9937'
  return '#DD2C1D'
}

const formatTimestamp = (iso: string): string => {
  try {
    return new Date(iso).toLocaleTimeString()
  } catch {
    return iso
  }
}
</script>

<template>
  <div class="max-w-4xl mx-auto px-4 py-6 md:py-8">
    <div
      class="backdrop-blur-sm rounded-xl p-6 mb-6"
      style="background-color: rgba(0, 77, 100, 0.4); border: 1px solid rgba(65, 185, 195, 0.3)"
    >
      <h1 class="text-white text-2xl mb-4 flex items-center gap-2">
        <Wifi class="w-6 h-6" style="color: #96EEF2" />
        Network Setup & Connection
      </h1>

      <!-- ── Current Mode Banner ────────────────────────────────── -->
      <div
        v-if="isApMode"
        class="rounded-lg p-4 mb-4 flex items-center gap-3"
        style="background-color: rgba(252, 216, 105, 0.1); border: 1px solid rgba(252, 216, 105, 0.3)"
      >
        <Smartphone class="w-5 h-5 flex-shrink-0" style="color: #FCD869" />
        <div class="flex-1">
          <p style="color: #FCD869">Hotspot mode</p>
          <p class="text-sm" style="color: #96EEF2">Broadcasting <span class="font-mono">{{ dorisHotspotName }}</span> — connect a client to reach DORIS.</p>
        </div>
      </div>

      <div
        v-if="isStaPending"
        class="rounded-lg p-4 mb-4 flex items-center gap-3"
        style="background-color: rgba(65, 185, 195, 0.1); border: 1px solid rgba(65, 185, 195, 0.3)"
      >
        <RefreshCw class="w-5 h-5 animate-spin flex-shrink-0" style="color: #41B9C3" />
        <div class="flex-1">
          <p style="color: #41B9C3">Switching to client mode…</p>
          <p class="text-sm" style="color: #96EEF2">
            Joining <span class="font-mono">{{ wlanState?.target_ssid ?? pendingSwitch?.ssid ?? '…' }}</span>.
            The DORIS hotspot is going down — your browser will lose this page within seconds.
          </p>
          <p class="text-sm mt-1" style="color: #96EEF2">
            On success, find DORIS at <span class="font-mono">http://doris.local:8095</span> on the same network.
            On failure, the hotspot will restart and you can reconnect here to see what went wrong.
          </p>
        </div>
      </div>

      <div
        v-if="isStaConnected"
        class="rounded-lg p-4 mb-4"
        style="background-color: rgba(150, 238, 242, 0.1); border: 1px solid rgba(150, 238, 242, 0.4)"
      >
        <div class="flex items-center gap-3 mb-3">
          <CheckCircle class="w-5 h-5 flex-shrink-0" style="color: #96EEF2" />
          <div class="flex-1">
            <p style="color: #96EEF2">Connected to client WLAN</p>
            <p class="text-sm" style="color: #96EEF2">
              Network: <span class="font-mono">{{ wlanState?.target_ssid ?? '—' }}</span>
            </p>
            <p v-if="wlanState?.ip_address" class="text-sm mt-1" style="color: #96EEF2">
              Reach DORIS at
              <span class="font-mono">http://doris.local:8095</span>
              or
              <span class="font-mono">http://{{ wlanState.ip_address }}:8095</span>
            </p>
          </div>
        </div>
        <div
          class="rounded-lg p-3 mb-3"
          style="background-color: rgba(255, 153, 55, 0.1); border: 1px solid rgba(255, 153, 55, 0.3)"
        >
          <div class="flex items-start gap-2">
            <AlertCircle class="w-4 h-4 flex-shrink-0 mt-0.5" style="color: #FF9937" />
            <p class="text-xs" style="color: #FF9937">
              The DORIS hotspot is down while in client mode. Power-cycling DORIS will always restore the hotspot.
            </p>
          </div>
        </div>
        <button
          @click="handleRestoreHotspot"
          :disabled="switching"
          class="w-full px-4 py-2 text-white rounded-lg transition-all disabled:opacity-50 hover:opacity-90 flex items-center justify-center gap-2"
          style="background: linear-gradient(135deg, #FCD869 0%, #FF9937 100%)"
        >
          <ArrowLeftRight class="w-4 h-4" />
          {{ switching ? 'Restoring…' : 'Restore DORIS Hotspot' }}
        </button>
      </div>

      <!-- ── Last Attempt Failure Banner ───────────────────────── -->
      <div
        v-if="failureBanner"
        class="rounded-lg p-4 mb-4"
        style="background-color: rgba(221, 44, 29, 0.1); border: 1px solid rgba(221, 44, 29, 0.3)"
      >
        <div class="flex items-start gap-3">
          <AlertCircle class="w-5 h-5 flex-shrink-0 mt-0.5" style="color: #DD2C1D" />
          <div class="flex-1">
            <p style="color: #DD2C1D">
              Last attempt to join <span class="font-mono">{{ failureBanner.ssid }}</span> failed
              <span class="text-xs" style="opacity: 0.7">({{ formatTimestamp(failureBanner.timestamp) }})</span>
            </p>
            <p v-if="failureBanner.error" class="text-sm mt-1" style="color: #DD2C1D; opacity: 0.85">
              {{ failureBanner.error }}
            </p>
            <p class="text-sm mt-2" style="color: #DD2C1D; opacity: 0.85">
              Hotspot has been restored. Double-check the password and signal strength, then try again.
            </p>
          </div>
          <button
            @click="dismissFailureBanner"
            class="text-xs px-2 py-1 rounded hover:bg-white/10"
            style="color: #DD2C1D"
          >
            Dismiss
          </button>
        </div>
      </div>

      <!-- ── Device Information ─────────────────────────────────── -->
      <div
        class="rounded-lg p-4 mb-4"
        style="background-color: rgba(14, 36, 70, 0.6); border: 1px solid rgba(65, 185, 195, 0.3)"
      >
        <h3 class="text-white mb-2 text-sm font-semibold">Device Information</h3>
        <div class="space-y-1">
          <div class="flex items-center justify-between">
            <span class="text-sm" style="color: #96EEF2">Serial Number:</span>
            <span class="text-sm font-mono text-white">{{ apiSerialNumber ?? '—' }}</span>
          </div>
          <div class="flex items-center justify-between">
            <span class="text-sm" style="color: #96EEF2">MAC Address:</span>
            <span class="text-sm font-mono text-white">{{ dorisMACAddress }}</span>
          </div>
          <div class="flex items-center justify-between">
            <span class="text-sm" style="color: #96EEF2">Hotspot Name:</span>
            <span class="text-sm font-mono text-white">{{ dorisHotspotName }}</span>
          </div>
        </div>
        <div class="mt-3 pt-3" style="border-top: 1px solid rgba(65, 185, 195, 0.2)">
          <p class="text-xs" style="color: #96EEF2">
            <strong>Note:</strong> Use the MAC address above when adding DORIS to high-security networks or MAC filtering systems.
          </p>
        </div>
      </div>

      <!-- ── Info Box ────────────────────────────────────────────── -->
      <div
        class="rounded-lg p-4 mb-6"
        style="background-color: rgba(65, 185, 195, 0.1); border: 1px solid rgba(65, 185, 195, 0.3)"
      >
        <div class="flex items-start gap-3">
          <AlertCircle class="w-5 h-5 flex-shrink-0 mt-0.5" style="color: #41B9C3" />
          <div class="space-y-2">
            <p class="text-sm" style="color: #96EEF2">
              The DORIS hotspot radio can either broadcast the DORIS hotspot <strong>or</strong>
              connect to a local WLAN — not both at the same time. Joining a local network gives
              DORIS internet access at the cost of taking the hotspot down for the duration of
              the session.
            </p>
            <p class="text-sm" style="color: #96EEF2">
              <strong>2.4 GHz networks only.</strong> The external antenna is single-band 2.4 GHz,
              so 5 GHz access points cannot be joined even when they appear in the scan list. Pick a
              2.4 GHz SSID; if your router broadcasts both bands under the same name, make sure the
              2.4 GHz radio is enabled.
            </p>
            <p class="text-sm" style="color: #96EEF2">
              <strong>Startup behavior:</strong> Every time DORIS is restarted, the hotspot radio
              reverts to hotspot mode. Saved networks are kept and will appear in the scan list,
              but they are never auto-joined.
            </p>
          </div>
        </div>
      </div>

      <!-- ── Scan Button ────────────────────────────────────────── -->
      <div class="flex items-center gap-3 mb-6">
        <button
          @click="handleScan"
          :disabled="isScanning || isStaPending"
          class="flex items-center gap-2 px-4 py-2 text-white rounded-lg transition-all disabled:opacity-50 hover:opacity-90"
          style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
        >
          <RefreshCw :class="['w-4 h-4', isScanning && 'animate-spin']" />
          {{ isScanning ? 'Scanning...' : 'Scan Networks' }}
        </button>
        <span class="text-sm" style="color: #96EEF2">
          {{ networks.length }} networks found
        </span>
      </div>

      <!-- ── Available Networks ────────────────────────────────── -->
      <div class="space-y-2 mb-6">
        <h2 class="text-white mb-3">Available Networks</h2>
        <div
          v-for="(network, index) in networks"
          :key="network.ssid + index"
          @click="selectedNetwork = network"
          class="rounded-lg p-4 cursor-pointer transition-all"
          :style="selectedNetwork?.ssid === network.ssid
            ? { backgroundColor: 'rgba(65, 185, 195, 0.2)', border: '1px solid #41B9C3' }
            : { backgroundColor: 'rgba(14, 36, 70, 0.5)', border: '1px solid rgba(65, 185, 195, 0.2)' }"
        >
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-3 flex-1">
              <div class="flex items-center gap-2">
                <Signal class="w-5 h-5" :style="{ color: getSignalColor(network.signal) }" />
                <CheckCircle v-if="network.saved" class="w-4 h-4 text-green-400" />
              </div>
              <div class="flex-1">
                <div class="flex items-center gap-2">
                  <p class="text-white">{{ network.ssid }}</p>
                  <span
                    v-if="network.connected"
                    class="text-xs px-2 py-0.5 rounded"
                    style="background-color: rgba(252, 216, 105, 0.2); color: #FCD869"
                  >
                    Connected
                  </span>
                  <span
                    v-else-if="network.saved"
                    class="text-xs bg-green-500/20 text-green-400 px-2 py-0.5 rounded"
                  >
                    Saved
                  </span>
                </div>
                <div class="flex items-center gap-3 mt-1">
                  <span class="text-sm" style="color: #96EEF2">{{ network.frequency }}</span>
                  <span class="text-sm flex items-center gap-1" style="color: #96EEF2">
                    <Lock class="w-3 h-3" />
                    {{ network.security }}
                  </span>
                </div>
              </div>
            </div>
            <span class="text-sm" style="color: #41B9C3">{{ network.signal }}%</span>
          </div>

          <!-- Password Input + Switch button for unsaved selected network -->
          <div
            v-if="selectedNetwork?.ssid === network.ssid && !network.saved"
            class="mt-4 pt-4"
            style="border-top: 1px solid rgba(65, 185, 195, 0.2)"
            @click.stop
          >
            <input
              type="password"
              v-model="password"
              placeholder="Enter password"
              class="w-full px-4 py-2 text-white rounded-lg focus:outline-none mb-3"
              style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
            />
            <button
              @click="handleConnectFromList"
              :disabled="!password || isStaPending"
              class="w-full px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2"
              style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
            >
              <ArrowLeftRight class="w-4 h-4" />
              Switch DORIS to this network
            </button>
          </div>

          <!-- Switch button for saved selected network (no password needed) -->
          <div
            v-else-if="selectedNetwork?.ssid === network.ssid && network.saved"
            class="mt-4 pt-4"
            style="border-top: 1px solid rgba(65, 185, 195, 0.2)"
            @click.stop
          >
            <button
              @click="handleConnectFromList"
              :disabled="isStaPending"
              class="w-full px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2"
              style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
            >
              <ArrowLeftRight class="w-4 h-4" />
              Switch DORIS to this saved network
            </button>
          </div>
        </div>
      </div>

      <!-- ── Advanced Options ──────────────────────────────────── -->
      <div class="pt-6" style="border-top: 1px solid rgba(65, 185, 195, 0.2)">
        <button
          @click="showAdvanced = !showAdvanced"
          class="transition-colors mb-4 hover:opacity-80"
          style="color: #41B9C3"
        >
          {{ showAdvanced ? '− Hide' : '+ Show' }} Advanced Options
        </button>

        <div v-if="showAdvanced" class="space-y-4">
          <!-- Manual Connection -->
          <div
            class="rounded-lg p-4"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)"
          >
            <h3 class="text-white mb-3">Manual Network Entry</h3>
            <p class="text-sm mb-4" style="color: #96EEF2">
              Enter network details manually if your network isn't detected in the scan
              (e.g., a hidden SSID).
            </p>
            <div class="space-y-3">
              <input
                type="text"
                v-model="manualSSID"
                placeholder="Network Name (SSID)"
                class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
              />
              <input
                type="password"
                v-model="manualPassword"
                placeholder="Password"
                class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
              />
              <button
                @click="handleManualConnect"
                :disabled="!manualSSID || isStaPending"
                class="w-full px-4 py-2 text-white rounded-lg transition-all disabled:opacity-50 flex items-center justify-center gap-2"
                style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
              >
                <ArrowLeftRight class="w-4 h-4" />
                Switch DORIS to this network
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ── Switch Confirmation Modal ─────────────────────────── -->
    <div
      v-if="showSwitchConfirm && pendingSwitch"
      class="fixed inset-0 z-50 flex items-center justify-center p-4"
      style="background-color: rgba(0, 0, 0, 0.7)"
      @click.self="cancelSwitch"
    >
      <div
        class="max-w-md w-full rounded-xl p-6"
        style="background-color: #0E2446; border: 1px solid rgba(65, 185, 195, 0.4)"
      >
        <h2 class="text-white text-lg mb-3 flex items-center gap-2">
          <ArrowLeftRight class="w-5 h-5" style="color: #FCD869" />
          Switch DORIS to client mode?
        </h2>
        <div
          class="rounded-lg p-3 mb-3"
          style="background-color: rgba(255, 153, 55, 0.1); border: 1px solid rgba(255, 153, 55, 0.3)"
        >
          <p class="text-sm" style="color: #FF9937">
            DORIS will leave hotspot mode and try to join
            <span class="font-mono">{{ pendingSwitch.ssid }}</span>.
            Anyone connected to the DORIS hotspot will be disconnected immediately.
          </p>
        </div>
        <ul class="text-sm space-y-2 mb-4" style="color: #96EEF2">
          <li class="flex gap-2">
            <span style="color: #FCD869">•</span>
            <span>If the join succeeds, reach DORIS at <span class="font-mono">http://doris.local:8095</span> on the same network.</span>
          </li>
          <li class="flex gap-2">
            <span style="color: #FCD869">•</span>
            <span>If the join fails, the hotspot will be restarted automatically and you can reconnect here.</span>
          </li>
          <li class="flex gap-2">
            <span style="color: #FCD869">•</span>
            <span>Power cycling DORIS always restores the hotspot.</span>
          </li>
        </ul>
        <div class="flex gap-3">
          <button
            @click="cancelSwitch"
            class="flex-1 px-4 py-2 text-white rounded-lg transition-all hover:opacity-90"
            style="background-color: rgba(255, 255, 255, 0.1); border: 1px solid rgba(255, 255, 255, 0.2)"
          >
            Cancel
          </button>
          <button
            @click="confirmSwitch"
            class="flex-1 px-4 py-2 text-white rounded-lg transition-all hover:opacity-90"
            style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
          >
            Switch now
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
