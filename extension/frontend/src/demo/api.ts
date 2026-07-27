/**
 * Fetch interceptor for demo mode.
 *
 * Every REST call in the app is a relative `/api/v1/...` URL, so patching
 * `fetch` once covers `useApi.ts`, `useDownloads.ts`, and the direct calls
 * in `App.vue`, `SensorConfiguration.vue`, `AllMissions.vue`, and
 * `ViewMediaScreen.vue` without touching any of them.
 *
 * Anything that is not `/api/v1/*` (fonts, source maps, assets) falls
 * through to the real `fetch` untouched.
 */
import * as f from './fixtures'

type Handler = (ctx: {
  url: URL
  init: RequestInit
  match: RegExpMatchArray
}) => unknown | Promise<unknown>

interface Route {
  method: string
  pattern: RegExp
  handler: Handler
}

const API_PREFIX = '/api/v1'

/** Mutable slices so the demo responds to the reviewer's own actions. */
const state = {
  notifications: f.notifications.map(n => ({ ...n })),
  notificationSettings: { ...f.notificationSettings },
  configurations: f.configurations.map(c => ({ ...c })),
  wlan: { ...f.wlanState },
  lightBrightness: 0,
}

function jsonBody(init: RequestInit): Record<string, unknown> {
  if (typeof init.body !== 'string') return {}
  try {
    return JSON.parse(init.body) as Record<string, unknown>
  } catch {
    return {}
  }
}

/**
 * A canvas-drawn stand-in for the live camera preview, so the sensor page
 * shows something plausible without shipping binary fixtures.
 */
function snapshotBlob(): Promise<Blob> {
  const canvas = document.createElement('canvas')
  canvas.width = 640
  canvas.height = 480
  const ctx = canvas.getContext('2d')!

  const water = ctx.createLinearGradient(0, 0, 0, canvas.height)
  water.addColorStop(0, '#0b3d5c')
  water.addColorStop(1, '#041c2b')
  ctx.fillStyle = water
  ctx.fillRect(0, 0, canvas.width, canvas.height)

  // Suspended particulate, seeded off the clock so the preview visibly
  // refreshes when the page polls it.
  const seed = Date.now() / 1000
  for (let i = 0; i < 220; i++) {
    const x = (Math.sin(seed + i * 12.9898) * 43758.5453) % 1
    const y = (Math.sin(seed + i * 78.233) * 43758.5453) % 1
    ctx.fillStyle = `rgba(200, 230, 255, ${0.05 + Math.abs(x) * 0.25})`
    ctx.beginPath()
    ctx.arc(Math.abs(x) * canvas.width, Math.abs(y) * canvas.height, Math.abs(y) * 2 + 0.4, 0, Math.PI * 2)
    ctx.fill()
  }

  ctx.fillStyle = 'rgba(255, 255, 255, 0.85)'
  ctx.font = '600 20px sans-serif'
  ctx.textAlign = 'center'
  ctx.fillText('DEMO CAMERA PREVIEW', canvas.width / 2, canvas.height / 2 - 8)
  ctx.font = '400 13px sans-serif'
  ctx.fillStyle = 'rgba(255, 255, 255, 0.55)'
  ctx.fillText('no vehicle connected', canvas.width / 2, canvas.height / 2 + 16)

  return new Promise(resolve => {
    canvas.toBlob(blob => resolve(blob ?? new Blob([], { type: 'image/jpeg' })), 'image/jpeg', 0.85)
  })
}

const routes: Route[] = [
  // ── System ──
  { method: 'GET', pattern: /^\/system\/status$/, handler: () => f.systemStatus },
  { method: 'GET', pattern: /^\/system\/battery$/, handler: () => f.battery },
  { method: 'GET', pattern: /^\/system\/storage$/, handler: () => f.storage },
  { method: 'GET', pattern: /^\/system\/storage\/migration$/, handler: () => f.storageMigration },
  { method: 'GET', pattern: /^\/system\/location$/, handler: () => f.location },
  { method: 'GET', pattern: /^\/system\/time$/, handler: () => f.systemTime },
  { method: 'POST', pattern: /^\/system\/time$/, handler: () => ({ synced: true, drift_seconds: 0.0, clock_sane: true }) },
  { method: 'GET', pattern: /^\/health$/, handler: () => ({ status: 'ok' }) },

  // ── Sensors ──
  { method: 'GET', pattern: /^\/sensors\/modules$/, handler: () => f.sensorModules },
  {
    method: 'GET',
    pattern: /^\/sensors\/([^/]+)\/readings$/,
    handler: ({ match }) => f.sensorReadings[match[1]] ?? [],
  },
  { method: 'PUT', pattern: /^\/sensors\/[^/]+\/config$/, handler: ({ init }) => ({ success: true, ...jsonBody(init) }) },
  { method: 'POST', pattern: /^\/sensors\/barometer\/calibrate$/, handler: () => ({ success: true, message: 'Barometer calibrated' }) },
  {
    method: 'POST',
    pattern: /^\/lights\/brightness$/,
    handler: ({ init }) => {
      const body = jsonBody(init)
      if (typeof body.brightness === 'number') state.lightBrightness = body.brightness
      return { success: true, brightness: state.lightBrightness }
    },
  },

  // ── Network ──
  { method: 'GET', pattern: /^\/network$/, handler: () => f.networkInfo },
  { method: 'GET', pattern: /^\/network\/status$/, handler: () => f.connectionStatus },
  { method: 'GET', pattern: /^\/network\/scan$/, handler: () => f.availableNetworks },
  {
    method: 'POST',
    pattern: /^\/network\/connect$/,
    handler: ({ init }) => ({ ...f.connectionStatus, ssid: String(jsonBody(init).ssid ?? f.connectionStatus.ssid) }),
  },
  { method: 'POST', pattern: /^\/network\/disconnect$/, handler: () => ({ ...f.connectionStatus, is_connected: false, ssid: null }) },
  { method: 'DELETE', pattern: /^\/network\/saved\/[^/]+$/, handler: () => ({ success: true }) },
  { method: 'GET', pattern: /^\/network\/wlan\/status$/, handler: () => state.wlan },
  {
    method: 'POST',
    pattern: /^\/network\/wlan\/connect$/,
    handler: ({ init }) => {
      const ssid = String(jsonBody(init).ssid ?? 'Unknown')
      state.wlan = {
        mode: 'sta_connected',
        target_ssid: ssid,
        ip_address: '10.0.4.117',
        last_attempt: { ssid, status: 'success', error: null, timestamp: new Date().toISOString() },
      }
      return state.wlan
    },
  },
  {
    method: 'POST',
    pattern: /^\/network\/wlan\/disconnect$/,
    handler: () => {
      state.wlan = { mode: 'ap', target_ssid: null, ip_address: null, last_attempt: state.wlan.last_attempt }
      return state.wlan
    },
  },

  // ── Dive ──
  { method: 'GET', pattern: /^\/dive\/status$/, handler: () => f.diveStatus },
  { method: 'GET', pattern: /^\/dive\/mission$/, handler: () => ({ mission: f.diveMission }) },
  { method: 'GET', pattern: /^\/dive\/history$/, handler: () => f.diveHistory },
  { method: 'DELETE', pattern: /^\/dive\/history\/[^/]+$/, handler: () => ({ success: true }) },
  { method: 'POST', pattern: /^\/dive\/start$/, handler: () => ({ success: true, message: 'Dive armed (demo)' }) },
  { method: 'POST', pattern: /^\/dive\/stop$/, handler: () => ({ success: true, message: 'Dive stopped (demo)' }) },
  { method: 'POST', pattern: /^\/dive\/sitl\/simulate_drop$/, handler: () => ({ success: true, message: 'Simulated drop (demo)' }) },
  { method: 'GET', pattern: /^\/vehicle\/arming$/, handler: () => f.armingStatus },

  // ── Missions ──
  { method: 'GET', pattern: /^\/missions$/, handler: () => f.missions },
  {
    method: 'GET',
    pattern: /^\/missions\/([^/]+)$/,
    handler: ({ match }) => f.missions.find(m => m.id === match[1]) ?? f.missions[0],
  },
  { method: 'POST', pattern: /^\/missions$/, handler: ({ init }) => ({ ...f.missions[0], ...jsonBody(init) }) },
  { method: 'POST', pattern: /^\/missions\/[^/]+\/start$/, handler: () => ({ success: true, message: 'Mission started (demo)' }) },
  { method: 'POST', pattern: /^\/missions\/[^/]+\/stop$/, handler: () => ({ success: true, message: 'Mission stopped (demo)' }) },
  { method: 'DELETE', pattern: /^\/missions\/[^/]+$/, handler: () => ({ success: true }) },

  // ── Media ──
  {
    method: 'GET',
    pattern: /^\/media\/files$/,
    handler: ({ url }) => {
      const type = url.searchParams.get('media_type')
      const mission = url.searchParams.get('mission_id')
      return f.mediaFiles.filter(
        m => (!type || m.media_type === type) && (!mission || m.mission_id === mission),
      )
    },
  },
  { method: 'DELETE', pattern: /^\/media\/files$/, handler: () => ({ success: true, deleted: 0 }) },
  { method: 'GET', pattern: /^\/media\/missions$/, handler: () => f.mediaMissions },
  { method: 'GET', pattern: /^\/media\/sync\/status$/, handler: () => f.syncStatus },
  { method: 'POST', pattern: /^\/media\/sync\/.*$/, handler: () => ({ success: true, message: 'Sync unavailable in demo' }) },

  // ── Configurations ──
  { method: 'GET', pattern: /^\/configurations$/, handler: () => state.configurations.map(c => ({ name: c.name, created_at: c.created_at, updated_at: c.updated_at })) },
  {
    method: 'GET',
    pattern: /^\/configurations\/([^/]+)$/,
    handler: ({ match }) => {
      const name = decodeURIComponent(match[1])
      return state.configurations.find(c => c.name === name) ?? state.configurations[0]
    },
  },
  {
    method: 'POST',
    pattern: /^\/configurations$/,
    handler: ({ init }) => {
      const body = jsonBody(init) as unknown as (typeof state.configurations)[number]
      const now = new Date().toISOString()
      const saved = { ...body, updated_at: now, created_at: body.created_at ?? now }
      const existing = state.configurations.findIndex(c => c.name === saved.name)
      if (existing >= 0) state.configurations[existing] = saved
      else state.configurations.push(saved)
      return saved
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/configurations\/([^/]+)$/,
    handler: ({ match }) => {
      const name = decodeURIComponent(match[1])
      state.configurations = state.configurations.filter(c => c.name !== name)
      return { success: true }
    },
  },

  // ── Notifications ──
  { method: 'GET', pattern: /^\/notifications$/, handler: () => state.notifications },
  { method: 'GET', pattern: /^\/notifications\/unread-count$/, handler: () => ({ count: state.notifications.filter(n => !n.read).length }) },
  { method: 'GET', pattern: /^\/notifications\/settings$/, handler: () => state.notificationSettings },
  {
    // PUT, not POST — the caller replaces its local settings object with
    // this response body, so a wrong method here breaks the toggles.
    method: 'PUT',
    pattern: /^\/notifications\/settings$/,
    handler: ({ init }) => {
      state.notificationSettings = { ...state.notificationSettings, ...jsonBody(init) }
      return state.notificationSettings
    },
  },
  {
    method: 'POST',
    pattern: /^\/notifications\/read-all$/,
    handler: () => {
      state.notifications.forEach(n => { n.read = true })
      return { success: true }
    },
  },
  {
    method: 'POST',
    pattern: /^\/notifications\/([^/]+)\/read$/,
    handler: ({ match }) => {
      const id = decodeURIComponent(match[1])
      const item = state.notifications.find(n => n.id === id)
      if (item) item.read = true
      return { success: true }
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/notifications\/([^/]+)$/,
    handler: ({ match }) => {
      const id = decodeURIComponent(match[1])
      state.notifications = state.notifications.filter(n => n.id !== id)
      return { success: true }
    },
  },

  // ── Artemis ──
  { method: 'GET', pattern: /^\/artemis\/ports$/, handler: () => f.serialPorts },
  { method: 'POST', pattern: /^\/artemis\/firmware\/upload$/, handler: () => ({ path: '/tmp/demo-firmware.bin', size_bytes: 262_144 }) },
  { method: 'POST', pattern: /^\/artemis\/flash$/, handler: () => ({ success: false, message: 'Firmware flashing is disabled in demo mode' }) },
  {
    method: 'GET',
    pattern: /^\/artemis\/flash\/status/,
    handler: () => ({
      session_id: 'demo',
      lines: ['Firmware flashing is disabled in demo mode.'],
      total_lines: 1,
      done: true,
      success: false,
      error: 'Demo mode',
    }),
  },

  // ── Tracker ──
  { method: 'GET', pattern: /^\/tracker\/gps$/, handler: () => f.trackerGps },
  { method: 'GET', pattern: /^\/tracker\/iridium-status$/, handler: () => f.trackerIridiumStatus },
  { method: 'POST', pattern: /^\/tracker\/iridium-test$/, handler: () => ({ success: true, message: 'Test message queued (demo)' }) },
  { method: 'POST', pattern: /^\/tracker\/debug$/, handler: () => ({ success: true, message: 'AGT_DEBUG sent (demo)' }) },

  // ── IP camera recording ──
  { method: 'GET', pattern: /^\/ipcam\/record$/, handler: () => ({ recording: false }) },
  { method: 'POST', pattern: /^\/ipcam\/record$/, handler: () => ({ success: true, recording: false, message: 'Recording unavailable in demo' }) },
]

/** Endpoints that return something other than JSON. */
async function binaryRoute(pathname: string, url: URL): Promise<Response | null> {
  if (pathname === '/camera/snapshot') {
    return new Response(await snapshotBlob(), {
      status: 200,
      headers: { 'Content-Type': 'image/jpeg' },
    })
  }

  if (pathname.endsWith('/export/scientific.csv')) {
    const csv = [
      'timestamp,depth_m,temperature_c,salinity_psu,heading_deg',
      '2026-07-26T14:32:10Z,0.4,14.20,29.80,138.5',
      '2026-07-26T14:38:44Z,24.7,12.65,30.02,141.2',
      '2026-07-26T14:51:03Z,62.4,11.42,30.18,139.8',
      '2026-07-26T15:44:19Z,18.2,13.10,29.95,144.0',
    ].join('\n')
    return new Response(csv, {
      status: 200,
      headers: { 'Content-Type': 'text/csv' },
    })
  }

  if (pathname === '/media/download') {
    const path = url.searchParams.get('path') ?? 'file'
    const note = `This is a placeholder for "${path}".\n\nDemo mode serves no real media, so downloads return this note instead.\n`
    return new Response(note, {
      status: 200,
      headers: { 'Content-Type': 'text/plain' },
    })
  }

  return null
}

/** Loading states are part of the design; keep them visible. */
function latency(): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, 60 + Math.random() * 180))
}

export function installApiMock(): void {
  const realFetch = globalThis.fetch.bind(globalThis)

  globalThis.fetch = async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
    const raw =
      typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const url = new URL(raw, window.location.origin)

    if (!url.pathname.startsWith(API_PREFIX)) {
      return realFetch(input as RequestInfo, init)
    }

    const pathname = url.pathname.slice(API_PREFIX.length)
    const method = (
      init.method ?? (typeof input === 'object' && 'method' in input ? input.method : 'GET')
    ).toUpperCase()

    await latency()

    const binary = await binaryRoute(pathname, url)
    if (binary) return binary

    for (const route of routes) {
      if (route.method !== method) continue
      const match = pathname.match(route.pattern)
      if (!match) continue
      const data = await route.handler({ url, init, match })
      return new Response(JSON.stringify(data), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    // Unmocked endpoint: stay plausible rather than throwing, so one gap
    // never takes down a whole screen during a design review.
    console.warn(`[demo] unmocked ${method} ${url.pathname}`)
    const fallback = method === 'GET' ? {} : { success: true, message: 'Not available in demo mode' }
    return new Response(JSON.stringify(fallback), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}
