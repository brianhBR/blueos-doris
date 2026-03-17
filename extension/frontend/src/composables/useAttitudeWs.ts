/**
 * WebSocket composable for streaming live attitude data from MAVLink2Rest.
 *
 * Connects directly to the MAVLink2Rest WebSocket at port 6040 with
 * an ATTITUDE filter. In dev, Vite proxies /ws/mavlink to the vehicle.
 */
import { ref, readonly, onMounted, onUnmounted } from 'vue'

export interface AttitudeData {
  roll_rad: number
  pitch_rad: number
  yaw_rad: number
  roll_deg: number
  pitch_deg: number
  yaw_deg: number
}

export interface AttitudeRates {
  rollspeed: number
  pitchspeed: number
  yawspeed: number
}

const RECONNECT_DELAY = 2000
const MAVLINK2REST_PORT = 6040
const RAD_TO_DEG = 180 / Math.PI

function getWsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const hostname = window.location.hostname

  const isDev = import.meta.env.DEV
  if (isDev) {
    const port = window.location.port || (window.location.protocol === 'https:' ? '443' : '80')
    return `${protocol}//${hostname}:${port}/ws/mavlink?filter=ATTITUDE`
  }

  return `${protocol}//${hostname}:${MAVLINK2REST_PORT}/ws/mavlink?filter=ATTITUDE`
}

export function useAttitudeWs() {
  const attitude = ref<AttitudeData | null>(null)
  const rates = ref<AttitudeRates | null>(null)
  const connected = ref(false)
  const error = ref<string | null>(null)
  const timeBoot = ref(0)

  let ws: WebSocket | null = null
  let reconnectTimer: number | undefined
  let shouldReconnect = true

  function connect() {
    if (ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) return

    const url = getWsUrl()
    error.value = null

    try {
      ws = new WebSocket(url)

      ws.onopen = () => {
        connected.value = true
        error.value = null
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          const msg = data?.message
          if (!msg || msg.type !== 'ATTITUDE') return

          attitude.value = {
            roll_rad: msg.roll,
            pitch_rad: msg.pitch,
            yaw_rad: msg.yaw,
            roll_deg: msg.roll * RAD_TO_DEG,
            pitch_deg: msg.pitch * RAD_TO_DEG,
            yaw_deg: msg.yaw * RAD_TO_DEG,
          }

          rates.value = {
            rollspeed: msg.rollspeed,
            pitchspeed: msg.pitchspeed,
            yawspeed: msg.yawspeed,
          }

          timeBoot.value = msg.time_boot_ms
        } catch {
          // Ignore malformed messages
        }
      }

      ws.onclose = () => {
        connected.value = false
        if (shouldReconnect) {
          reconnectTimer = window.setTimeout(connect, RECONNECT_DELAY)
        }
      }

      ws.onerror = () => {
        error.value = 'WebSocket connection error'
        connected.value = false
      }
    } catch {
      error.value = 'Failed to create WebSocket connection'
    }
  }

  function disconnect() {
    shouldReconnect = false
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = undefined
    }
    if (ws) {
      ws.close()
      ws = null
    }
    connected.value = false
  }

  onMounted(() => {
    shouldReconnect = true
    connect()
  })

  onUnmounted(() => {
    disconnect()
  })

  return {
    attitude: readonly(attitude),
    rates: readonly(rates),
    connected: readonly(connected),
    error: readonly(error),
    timeBoot: readonly(timeBoot),
    connect,
    disconnect,
  }
}
