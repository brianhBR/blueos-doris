<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { Wifi, Lock, RefreshCw, Signal, AlertCircle, CheckCircle, Smartphone } from 'lucide-vue-next'
import { useWifiNetworks } from '../composables/useApi'

const emit = defineEmits<{
  connect: [connected: boolean]
}>()

const {
  networks: apiNetworks,
  connectionStatus: apiConnectionStatus,
  scanning,
  fetchNetworks,
  scanNetworks,
  connectToNetwork,
} = useWifiNetworks()

const dorisSerialNumber = 'D-2847-AQ'
const dorisMACAddress = computed(() => apiConnectionStatus.value?.mac_address ?? 'A4:CF:12:8B:3E:D1')
const dorisHotspotName = `DORIS_${dorisSerialNumber}`

const showAdvanced = ref(true)
const selectedNetwork = ref<DisplayNetwork | null>(null)
const password = ref('')
const connectionStatus = ref<'idle' | 'connecting' | 'connected' | 'failed'>('idle')
const manualSSID = ref('')
const manualPassword = ref('')
const useStaticIP = ref(false)
const staticIPAddress = ref('')
const subnetMask = ref('255.255.255.0')
const gateway = ref('')
const dnsServer = ref('')

interface DisplayNetwork {
  ssid: string
  signal: number
  frequency: string
  security: string
  saved: boolean
}

const networks = computed<DisplayNetwork[]>(() => {
  if (apiNetworks.value.length > 0) {
    return apiNetworks.value.map((n) => ({
      ssid: n.ssid,
      signal: n.signal_strength,
      frequency: n.frequency,
      security: n.security,
      saved: n.is_saved,
    }))
  }
  return []
})

let pollInterval: number | undefined

onMounted(() => {
  fetchNetworks()
  if (apiConnectionStatus.value?.is_connected) {
    connectionStatus.value = 'connected'
    emit('connect', true)
  }
  pollInterval = setInterval(fetchNetworks, 10000) as unknown as number
})

onUnmounted(() => {
  if (pollInterval) clearInterval(pollInterval)
})

const isScanning = computed(() => scanning.value)

const handleScan = async () => {
  await scanNetworks()
}

const handleConnect = async () => {
  if (!selectedNetwork.value) return
  connectionStatus.value = 'connecting'
  const ssid = selectedNetwork.value.ssid
  const pwd = selectedNetwork.value.saved ? '' : password.value
  const success = await connectToNetwork(ssid, pwd)
  if (success) {
    connectionStatus.value = 'connected'
    emit('connect', true)
  } else {
    connectionStatus.value = 'failed'
  }
}

const handleManualConnect = async () => {
  if (!manualSSID.value || !manualPassword.value) return
  connectionStatus.value = 'connecting'
  const success = await connectToNetwork(manualSSID.value, manualPassword.value)
  if (success) {
    connectionStatus.value = 'connected'
    emit('connect', true)
  } else {
    connectionStatus.value = 'failed'
  }
}

const getSignalColor = (signal: number) => {
  if (signal > 70) return '#FCD869'
  if (signal > 40) return '#FF9937'
  return '#DD2C1D'
}

const manualConnectDisabled = () => {
  if (!manualSSID.value || !manualPassword.value) return true
  if (useStaticIP.value && (!staticIPAddress.value || !subnetMask.value || !gateway.value)) return true
  return false
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

      <!-- Device Information -->
      <div
        class="rounded-lg p-4 mb-4"
        style="background-color: rgba(14, 36, 70, 0.6); border: 1px solid rgba(65, 185, 195, 0.3)"
      >
        <h3 class="text-white mb-2 text-sm font-semibold">Device Information</h3>
        <div class="space-y-1">
          <div class="flex items-center justify-between">
            <span class="text-sm" style="color: #96EEF2">Serial Number:</span>
            <span class="text-sm font-mono text-white">{{ dorisSerialNumber }}</span>
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

      <!-- Connection Status: Connected -->
      <div
        v-if="connectionStatus === 'connected'"
        class="rounded-lg p-4 mb-4 flex items-center gap-3"
        style="background-color: rgba(252, 216, 105, 0.1); border: 1px solid rgba(252, 216, 105, 0.3)"
      >
        <CheckCircle class="w-5 h-5" style="color: #FCD869" />
        <div>
          <p style="color: #FCD869">Connected Successfully</p>
          <p class="text-sm" style="color: #96EEF2">Network: {{ selectedNetwork?.ssid || manualSSID }}</p>
        </div>
      </div>

      <!-- Connection Status: Connecting -->
      <div
        v-if="connectionStatus === 'connecting'"
        class="rounded-lg p-4 mb-4 flex items-center gap-3"
        style="background-color: rgba(65, 185, 195, 0.1); border: 1px solid rgba(65, 185, 195, 0.3)"
      >
        <RefreshCw class="w-5 h-5 animate-spin" style="color: #41B9C3" />
        <p style="color: #41B9C3">Connecting to network...</p>
      </div>

      <!-- Connection Status: Failed -->
      <div
        v-if="connectionStatus === 'failed'"
        class="rounded-lg p-4 mb-4"
        style="background-color: rgba(221, 44, 29, 0.1); border: 1px solid rgba(221, 44, 29, 0.3)"
      >
        <div class="flex items-center gap-3 mb-2">
          <AlertCircle class="w-5 h-5" style="color: #DD2C1D" />
          <p style="color: #DD2C1D">Connection Failed</p>
        </div>
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
          <div class="space-y-2">
            <p class="text-sm" style="color: #96EEF2">
              DORIS supports both 2.4GHz and 5GHz networks. For best compatibility, 2.4GHz is recommended.
              Previously connected networks will reconnect automatically when available.
            </p>
            <p class="text-sm" style="color: #96EEF2">
              <strong>Startup Behavior:</strong> Every time DORIS is restarted, it automatically starts in hotspot mode. After startup, you will need to reconnect it to your preferred network.
            </p>
          </div>
        </div>
      </div>

      <!-- Scan Button -->
      <div class="flex items-center gap-3 mb-6">
        <button
          @click="handleScan"
          :disabled="isScanning"
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

      <!-- Available Networks -->
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
                    v-if="network.saved"
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

          <!-- Password Input for unsaved selected network -->
          <div
            v-if="selectedNetwork?.ssid === network.ssid && !network.saved"
            class="mt-4 pt-4"
            style="border-top: 1px solid rgba(65, 185, 195, 0.2)"
            @click.stop
          >
            <div
              v-if="network.ssid !== dorisHotspotName"
              class="rounded-lg p-3 mb-3"
              style="background-color: rgba(255, 153, 55, 0.1); border: 1px solid rgba(255, 153, 55, 0.3)"
            >
              <div class="flex items-start gap-2">
                <AlertCircle class="w-4 h-4 flex-shrink-0 mt-0.5" style="color: #FF9937" />
                <p class="text-xs" style="color: #FF9937">
                  <strong>Note:</strong> Once connected to this network, you will lose access to DORIS from the hotspot. You will need to access DORIS from the new network or power cycle DORIS to restore the hotspot.
                </p>
              </div>
            </div>
            <input
              type="password"
              v-model="password"
              placeholder="Enter password"
              class="w-full px-4 py-2 text-white rounded-lg focus:outline-none mb-3"
              style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
            />
            <button
              @click="handleConnect"
              class="w-full px-4 py-2 text-white rounded-lg transition-all hover:opacity-90"
              style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
            >
              Connect
            </button>
          </div>

          <!-- Connect button for saved selected network -->
          <div
            v-if="selectedNetwork?.ssid === network.ssid && network.saved"
            class="mt-4 pt-4"
            style="border-top: 1px solid rgba(65, 185, 195, 0.2)"
            @click.stop
          >
            <div
              v-if="network.ssid !== dorisHotspotName"
              class="rounded-lg p-3 mb-3"
              style="background-color: rgba(255, 153, 55, 0.1); border: 1px solid rgba(255, 153, 55, 0.3)"
            >
              <div class="flex items-start gap-2">
                <AlertCircle class="w-4 h-4 flex-shrink-0 mt-0.5" style="color: #FF9937" />
                <p class="text-xs" style="color: #FF9937">
                  <strong>Note:</strong> Once connected to this network, you will lose access to DORIS from the hotspot. You will need to access DORIS from the new network or power cycle DORIS to restore the hotspot.
                </p>
              </div>
            </div>
            <button
              @click="handleConnect"
              class="w-full px-4 py-2 text-white rounded-lg transition-all hover:opacity-90"
              style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
            >
              Connect
            </button>
          </div>
        </div>
      </div>

      <!-- Advanced Options -->
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

              <!-- Static IP Configuration -->
              <div class="pt-3" style="border-top: 1px solid rgba(65, 185, 195, 0.1)">
                <label class="flex items-center gap-2 mb-3 cursor-pointer">
                  <input
                    type="checkbox"
                    v-model="useStaticIP"
                    class="w-4 h-4 rounded"
                    style="accent-color: #41B9C3"
                  />
                  <span class="text-sm" style="color: #96EEF2">Use Static IP Address</span>
                </label>

                <div v-if="useStaticIP" class="space-y-3 pl-6">
                  <div>
                    <label class="block text-xs mb-1" style="color: #96EEF2">IP Address *</label>
                    <input
                      type="text"
                      v-model="staticIPAddress"
                      placeholder="e.g., 192.168.1.100"
                      class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                      style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
                    />
                  </div>
                  <div>
                    <label class="block text-xs mb-1" style="color: #96EEF2">Subnet Mask *</label>
                    <input
                      type="text"
                      v-model="subnetMask"
                      placeholder="e.g., 255.255.255.0"
                      class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                      style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
                    />
                  </div>
                  <div>
                    <label class="block text-xs mb-1" style="color: #96EEF2">Gateway *</label>
                    <input
                      type="text"
                      v-model="gateway"
                      placeholder="e.g., 192.168.1.1"
                      class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                      style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
                    />
                  </div>
                  <div>
                    <label class="block text-xs mb-1" style="color: #96EEF2">DNS Server (Optional)</label>
                    <input
                      type="text"
                      v-model="dnsServer"
                      placeholder="e.g., 8.8.8.8"
                      class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
                      style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
                    />
                  </div>
                  <div
                    class="rounded-lg p-3 mt-2"
                    style="background-color: rgba(65, 185, 195, 0.1); border: 1px solid rgba(65, 185, 195, 0.2)"
                  >
                    <p class="text-xs" style="color: #96EEF2">
                      <strong>Note:</strong> Ensure the static IP address is within the network's range and not already in use by another device.
                    </p>
                  </div>
                </div>
              </div>

              <button
                @click="handleManualConnect"
                class="w-full px-4 py-2 text-white rounded-lg transition-all disabled:opacity-50"
                style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
                :disabled="manualConnectDisabled()"
              >
                Connect to Manual Network
              </button>

              <div
                v-if="manualSSID && manualPassword"
                class="rounded-lg p-3 mt-3"
                style="background-color: rgba(255, 153, 55, 0.1); border: 1px solid rgba(255, 153, 55, 0.3)"
              >
                <div class="flex items-start gap-2">
                  <AlertCircle class="w-4 h-4 flex-shrink-0 mt-0.5" style="color: #FF9937" />
                  <p class="text-xs" style="color: #FF9937">
                    <strong>Note:</strong> Once connected to this network, you will lose access to DORIS from the hotspot. You will need to access DORIS from the new network or power cycle DORIS to restore the hotspot.
                  </p>
                </div>
              </div>
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
            <div
              class="rounded-lg p-3 mb-3"
              style="background-color: rgba(255, 153, 55, 0.1); border: 1px solid rgba(255, 153, 55, 0.3)"
            >
              <div class="flex items-start gap-2">
                <AlertCircle class="w-4 h-4 flex-shrink-0 mt-0.5" style="color: #FF9937" />
                <p class="text-xs" style="color: #FF9937">
                  <strong>Warning:</strong> Enabling hotspot mode will disconnect DORIS from its current network connection. You will need to reconnect your device to the DORIS hotspot ({{ dorisHotspotName }}) to access the interface.
                </p>
              </div>
            </div>
            <button
              class="w-full px-4 py-2 text-white rounded-lg transition-all hover:opacity-90"
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
