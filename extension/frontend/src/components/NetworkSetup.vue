<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { Wifi, Lock, RefreshCw, Signal, AlertCircle, Smartphone } from 'lucide-vue-next'
import { useWifiNetworks, type WifiNetwork } from '../composables/useApi'

const emit = defineEmits<{
  connect: [connected: boolean]
}>()

// API composable
const {
  networks: apiNetworks,
  connectionStatus: apiConnectionStatus,
  loading,
  scanning,
  error,
  fetchNetworks,
  scanNetworks,
  connectToNetwork,
  disconnectFromNetwork
} = useWifiNetworks()

const showAdvanced = ref(false)
const selectedNetwork = ref<WifiNetwork | null>(null)
const password = ref('')
const connectionState = ref<'idle' | 'connecting' | 'connected' | 'failed'>('idle')
const manualSSID = ref('')
const manualPassword = ref('')

// Transform API networks to component format
const networks = computed(() => {
  return apiNetworks.value.map(net => ({
    ssid: net.ssid,
    signal: net.signal_strength,
    frequency: net.frequency,
    security: net.security,
    saved: net.is_saved,
    isConnected: net.is_connected
  }))
})

// Update connection state based on API status
const updateConnectionState = () => {
  if (apiConnectionStatus.value?.is_connected) {
    connectionState.value = 'connected'
  }
}

const handleScan = async () => {
  await scanNetworks()
}

const handleConnect = async () => {
  if (!selectedNetwork.value) return

  connectionState.value = 'connecting'

  // If saved network, use empty password (will use saved credentials)
  const pwd = selectedNetwork.value.is_saved ? '' : password.value
  const success = await connectToNetwork(selectedNetwork.value.ssid, pwd)

  if (success) {
    connectionState.value = 'connected'
    emit('connect', true)
  } else {
    connectionState.value = 'failed'
  }
}

const handleManualConnect = async () => {
  if (manualSSID.value && manualPassword.value) {
    connectionState.value = 'connecting'
    const success = await connectToNetwork(manualSSID.value, manualPassword.value)

    if (success) {
      connectionState.value = 'connected'
      emit('connect', true)
    } else {
      connectionState.value = 'failed'
    }
  }
}

const getSignalColor = (signal: number) => {
  if (signal > 70) return '#FCD869'
  if (signal > 40) return '#FF9937'
  return '#DD2C1D'
}

// Fetch networks on mount
let refreshInterval: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  fetchNetworks()
  updateConnectionState()
  // Refresh every 10 seconds
  refreshInterval = setInterval(() => {
    fetchNetworks()
    updateConnectionState()
  }, 10000)
})

onUnmounted(() => {
  if (refreshInterval) clearInterval(refreshInterval)
})
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

      <!-- Connection Status -->
      <div
        v-if="connectionState === 'connected' || apiConnectionStatus?.is_connected"
        class="rounded-lg p-4 mb-4 flex items-center gap-3"
        style="background-color: rgba(252, 216, 105, 0.1); border: 1px solid rgba(252, 216, 105, 0.3)"
      >
        <Wifi class="w-5 h-5" style="color: #FCD869" />
        <div>
          <p style="color: #FCD869">Connected Successfully</p>
          <p class="text-sm" style="color: #96EEF2">Network: {{ apiConnectionStatus?.ssid || selectedNetwork?.ssid || manualSSID }}</p>
          <p v-if="apiConnectionStatus?.ip_address" class="text-sm" style="color: #96EEF2">IP: {{ apiConnectionStatus.ip_address }}</p>
        </div>
      </div>

      <div
        v-if="connectionState === 'connecting'"
        class="rounded-lg p-4 mb-4 flex items-center gap-3"
        style="background-color: rgba(65, 185, 195, 0.1); border: 1px solid rgba(65, 185, 195, 0.3)"
      >
        <RefreshCw class="w-5 h-5 animate-spin" style="color: #41B9C3" />
        <p style="color: #41B9C3">Connecting to network...</p>
      </div>

      <div
        v-if="connectionState === 'failed'"
        class="rounded-lg p-4 mb-4"
        style="background-color: rgba(221, 44, 29, 0.1); border: 1px solid rgba(221, 44, 29, 0.3)"
      >
        <div class="flex items-center gap-3 mb-2">
          <AlertCircle class="w-5 h-5" style="color: #DD2C1D" />
          <p style="color: #DD2C1D">Connection Failed</p>
        </div>
        <p v-if="error" class="text-sm mb-2" style="color: #DD2C1D">{{ error }}</p>
        <p class="text-sm" style="color: #DD2C1D; opacity: 0.8">Troubleshooting tips:</p>
        <ul class="text-sm list-disc ml-5 mt-1" style="color: #DD2C1D; opacity: 0.8">
          <li>Check SSID and password are correct</li>
          <li>Ensure router supports 2.4GHz (if 5GHz not supported)</li>
          <li>Try restarting the system and router</li>
        </ul>
      </div>

      <!-- Info Box -->
      <div
        class="rounded-lg p-4 mb-6"
        style="background-color: rgba(65, 185, 195, 0.1); border: 1px solid rgba(65, 185, 195, 0.3)"
      >
        <div class="flex items-start gap-3">
          <AlertCircle class="w-5 h-5 flex-shrink-0 mt-0.5" style="color: #41B9C3" />
          <div>
            <p class="text-sm" style="color: #96EEF2">
              DORIS supports both 2.4GHz and 5GHz networks. For best compatibility, 2.4GHz is recommended.
              Previously connected networks will reconnect automatically when available.
            </p>
          </div>
        </div>
      </div>

      <!-- Scan Button -->
      <div class="flex items-center gap-3 mb-6">
        <button
          @click="handleScan"
          :disabled="scanning"
          class="flex items-center gap-2 px-4 py-2 text-white rounded-lg transition-all disabled:opacity-50 hover:opacity-90"
          style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
        >
          <RefreshCw :class="['w-4 h-4', scanning && 'animate-spin']" />
          {{ scanning ? 'Scanning...' : 'Scan Networks' }}
        </button>
        <span class="text-sm" style="color: #96EEF2">
          {{ networks.length }} networks found
        </span>
        <span v-if="loading" class="text-sm" style="color: #41B9C3">Loading...</span>
      </div>

      <!-- Available Networks -->
      <div class="space-y-2 mb-6">
        <h2 class="text-white mb-3">Available Networks</h2>
        <div
          v-for="(network, index) in apiNetworks"
          :key="network.ssid + index"
          @click="selectedNetwork = selectedNetwork?.ssid === network.ssid ? null : network"
          class="rounded-lg p-4 cursor-pointer transition-all"
          :style="selectedNetwork?.ssid === network.ssid
            ? { backgroundColor: 'rgba(65, 185, 195, 0.2)', border: '1px solid #41B9C3' }
            : network.is_connected
              ? { backgroundColor: 'rgba(252, 216, 105, 0.1)', border: '1px solid rgba(252, 216, 105, 0.3)' }
              : { backgroundColor: 'rgba(14, 36, 70, 0.5)', border: '1px solid rgba(65, 185, 195, 0.2)' }"
        >
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-3 flex-1">
              <div class="flex items-center gap-2">
                <Signal class="w-5 h-5" :style="{ color: getSignalColor(network.signal_strength) }" />
              </div>
              <div class="flex-1">
                <div class="flex items-center gap-2">
                  <p class="text-white">{{ network.ssid }}</p>
                  <span
                    v-if="network.is_connected"
                    class="text-xs bg-yellow-500/20 text-yellow-400 px-2 py-0.5 rounded"
                  >
                    Connected
                  </span>
                  <span
                    v-else-if="network.is_saved"
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
            <span class="text-sm" style="color: #41B9C3">{{ network.signal_strength }}%</span>
          </div>

          <!-- Password Input for Selected Network (not saved) -->
          <div
            v-if="selectedNetwork?.ssid === network.ssid && !network.is_saved && !network.is_connected"
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
              @click="handleConnect"
              :disabled="loading"
              class="w-full px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 disabled:opacity-50"
              style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
            >
              {{ loading ? 'Connecting...' : 'Connect' }}
            </button>
          </div>

          <!-- Connect button for saved networks -->
          <div
            v-if="selectedNetwork?.ssid === network.ssid && network.is_saved && !network.is_connected"
            class="mt-4 pt-4"
            style="border-top: 1px solid rgba(65, 185, 195, 0.2)"
            @click.stop
          >
            <button
              @click="handleConnect"
              :disabled="loading"
              class="w-full px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 disabled:opacity-50"
              style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
            >
              {{ loading ? 'Connecting...' : 'Connect' }}
            </button>
          </div>

          <!-- Disconnect button for connected networks -->
          <div
            v-if="selectedNetwork?.ssid === network.ssid && network.is_connected"
            class="mt-4 pt-4"
            style="border-top: 1px solid rgba(65, 185, 195, 0.2)"
            @click.stop
          >
            <button
              @click="disconnectFromNetwork"
              :disabled="loading"
              class="w-full px-4 py-2 text-white rounded-lg transition-all hover:opacity-90 disabled:opacity-50"
              style="background: linear-gradient(135deg, #FF9937 0%, #DD2C1D 100%)"
            >
              {{ loading ? 'Disconnecting...' : 'Disconnect' }}
            </button>
          </div>
        </div>

        <!-- Empty state -->
        <div
          v-if="apiNetworks.length === 0 && !scanning && !loading"
          class="text-center py-8"
          style="color: #96EEF2"
        >
          <Wifi class="w-12 h-12 mx-auto mb-3 opacity-50" />
          <p>No networks found. Click "Scan Networks" to search.</p>
        </div>
      </div>

      <!-- Advanced Options -->
      <div class="pt-6" style="border-top: 1px solid rgba(65, 185, 195, 0.2)">
        <button
          @click="showAdvanced = !showAdvanced"
          class="transition-colors mb-4"
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
              Enter network details manually if your network isn't detected in the scan.
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
                class="w-full px-4 py-2 text-white rounded-lg transition-all disabled:opacity-50"
                style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
                :disabled="!manualSSID || !manualPassword"
              >
                Connect to Manual Network
              </button>
            </div>
          </div>

          <!-- Direct Connection Mode -->
          <div
            class="rounded-lg p-4"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.2)"
          >
            <h3 class="text-white mb-3 flex items-center gap-2">
              <Smartphone class="w-5 h-5" style="color: #41B9C3" />
              Direct Device Connection
            </h3>
            <p class="text-sm mb-3" style="color: #96EEF2">
              Connect directly to DORIS via hotspot mode when no WiFi network is available.
            </p>
            <button
              class="w-full px-4 py-2 text-white rounded-lg transition-all"
              style="background: linear-gradient(135deg, #FCD869 0%, #FF9937 100%)"
            >
              Enable DORIS Hotspot
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

