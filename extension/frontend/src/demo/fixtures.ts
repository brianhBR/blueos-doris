/**
 * Fixture data for demo mode.
 *
 * Every export is typed against the real interfaces in `useApi.ts`, so
 * `vue-tsc` fails the demo build if a backend response shape drifts and
 * the frontend types are updated to match. That is the only automatic
 * guard against fixtures silently going stale.
 *
 * Timestamps are computed relative to page load so the demo never looks
 * like an abandoned snapshot.
 */
import type {
  ArmingStatus,
  BatteryInfo,
  CameraSettings,
  ConfigurationSummary,
  ConnectionStatus,
  DeploymentConfiguration,
  DiveHistorySummary,
  DiveMissionState,
  DiveStatus,
  LightSettings,
  LocationInfo,
  MediaFile,
  MediaMission,
  MissionSummary,
  NetworkFullInfo,
  NotificationItemApi,
  NotificationSettingsApi,
  SensorModule,
  SensorReading,
  SerialPortInfo,
  StorageInfo,
  StorageMigrationStatus,
  SyncStatus,
  SystemStatus,
  TimeValue,
  WifiNetwork,
  WlanState,
} from '@/composables/useApi'

const BOOT = Date.now()

/** ISO timestamp `minutes` in the past, relative to page load. */
export function minutesAgo(minutes: number): string {
  return new Date(BOOT - minutes * 60_000).toISOString()
}

function daysAgo(days: number): string {
  return new Date(BOOT - days * 86_400_000).toISOString()
}

function time(number: string, unit: TimeValue['unit'] = 'seconds'): TimeValue {
  return { number, unit }
}

/** Field-for-field the defaults from models/configuration.py CameraSettings. */
function cameraSettings(overrides: Partial<CameraSettings> = {}): CameraSettings {
  return {
    enabled: false,
    camera_type: 'continuous-video',
    capture_frequency: 10,
    capture_frequency_unit: 'seconds',
    video_record: time('10'),
    video_pause: time('5'),
    timelapse_light_pre: time('2'),
    timelapse_light_post: time('1'),
    resolution: '4K',
    image_type: 'High-Rez JPG',
    file_format: 'JPEG',
    video_file_format: '.MP4',
    frame_rate: 30,
    focus: 'auto',
    iso: 'auto',
    white_balance: 'auto',
    exposure: '0',
    sharpness: 'medium',
    sleep_timer_enabled: false,
    sleep_timer: time('', 'hours'),
    ...overrides,
  }
}

/** Field-for-field the defaults from models/configuration.py LightSettings. */
function lightSettings(overrides: Partial<LightSettings> = {}): LightSettings {
  return {
    enabled: false,
    mode: 'continuous',
    brightness: 60,
    match_camera_interval: false,
    on_time: time('10'),
    off_time: time('5'),
    ...overrides,
  }
}

// ── System ──────────────────────────────────────────────────────────

export const systemStatus: SystemStatus = {
  connected: true,
  battery_level: 87,
  battery_voltage: 15.8,
  battery_time_remaining: '6h 12m',
  storage_used_percent: 27,
  storage_used_gb: 34.2,
  storage_total_gb: 128.0,
  cpu_usage: 18,
  memory_usage: 41,
  temperature: 38.4,
  uptime: '2h 47m',
}

export const battery: BatteryInfo = {
  level: 87,
  voltage: 15.8,
  current: 1.42,
  temperature: 24.1,
  time_remaining: '6h 12m',
  charging: false,
}

export const storage: StorageInfo = {
  total_gb: 128.0,
  used_gb: 34.2,
  available_gb: 93.8,
  used_percent: 27,
  storage_type: 'USB SSD',
}

export const location: LocationInfo = {
  latitude: 47.6205,
  longitude: -122.3493,
  altitude: 0.4,
  depth: 0.0,
  heading: 138.5,
  speed: 0.2,
  satellites: 11,
  fix_type: '3D Fix',
  last_update: minutesAgo(0.5),
}

export const storageMigration: StorageMigrationStatus = {
  state: 'done',
  message: 'External storage mounted at /usr/blueos/userdata',
  error: '',
}

export const systemTime = {
  synced: true,
  clock_sane: true,
  source: 'Artemis GPS',
  last_drift_seconds: 0.3,
}

// ── Sensors ─────────────────────────────────────────────────────────

/**
 * Mirrors what `SensorService.get_connected_modules` emits. Ids, name
 * formats, and module_status strings follow the backend services rather
 * than being invented.
 *
 * The service also enumerates ping/sonar devices, but this demo vehicle
 * has none attached, so no `ping-*` module appears.
 *
 * `power_usage` and `sample_rate` are left at the model defaults (0.0 and
 * null) because no service ever populates them.
 */
export const sensorModules: SensorModule[] = [
  {
    // services/sensors.py: id=f"camera-{stream.id}", name=f"Camera ({stream.name})"
    // The stream name is BlueOS Camera Manager config, not a code constant.
    id: 'camera-1',
    name: 'Camera (DORIS IP Camera)',
    type: 'camera',
    status: 'connected',
    module_status: 'Ready: Active',
    last_reading: minutesAgo(0.05),
    power_usage: 0.0,
    sample_rate: null,
    firmware_version: null,
  },
  {
    // services/lights.py: LIGHT_CHANNELS = {13: "Lights 1"}
    id: 'light-ch13',
    name: 'Lights 1 (SERVO13)',
    type: 'light',
    status: 'connected',
    module_status: 'Ready: Idle',
    last_reading: null,
    power_usage: 0.0,
    sample_rate: null,
    firmware_version: null,
  },
  {
    // services/barometer.py: module_status=f"Ready: {press_abs:.1f} hPa"
    id: 'barometer',
    name: 'Barometer',
    type: 'sensor',
    status: 'connected',
    module_status: 'Ready: 1013.2 hPa',
    last_reading: null,
    power_usage: 0.0,
    sample_rate: null,
    firmware_version: null,
  },
  {
    // services/barometer.py: module_status=f"Ready: {temp_c:.1f} °C"
    id: 'thermometer',
    name: 'Thermometer',
    type: 'sensor',
    status: 'connected',
    module_status: 'Ready: 11.4 °C',
    last_reading: null,
    power_usage: 0.0,
    sample_rate: null,
    firmware_version: null,
  },
  {
    // services/tracker.py: "{fix_type_name} | {lat:.6f}, {lon:.6f} | {sats} sats"
    id: 'artemis-tracker',
    name: 'Artemis Global Tracker',
    type: 'tracker',
    status: 'connected',
    module_status: '3D Fix | 47.601900, -122.378200 | 11 sats',
    last_reading: minutesAgo(0.5),
    power_usage: 0.0,
    sample_rate: null,
    firmware_version: '1.0.1',
  },
]

/**
 * `SensorService.get_sensor_readings` always returns an empty list — the
 * Ping Service exposes no per-sensor history — so the demo does the same.
 */
export const sensorReadings: Record<string, SensorReading[]> = {}

// ── Network ─────────────────────────────────────────────────────────

const connection: ConnectionStatus = {
  is_connected: true,
  ssid: 'DORIS (D-0042)',
  ip_address: '192.168.2.2',
  mac_address: 'b8:27:eb:4f:1a:9c',
  signal_strength: 92,
}

export const availableNetworks: WifiNetwork[] = [
  { ssid: 'RV Falkor Deck', signal_strength: 78, security: 'WPA2', frequency: '5 GHz', is_saved: true, is_connected: false },
  { ssid: 'Dock Office', signal_strength: 54, security: 'WPA2', frequency: '2.4 GHz', is_saved: false, is_connected: false },
  { ssid: 'Harbormaster Guest', signal_strength: 31, security: 'Open', frequency: '2.4 GHz', is_saved: false, is_connected: false },
]

export const networkInfo: NetworkFullInfo = {
  connection,
  available_networks: availableNetworks,
  is_scanning: false,
  serial_number: 'D-0042',
  hotspot_ssid: 'DORIS (D-0042)',
}

export const connectionStatus: ConnectionStatus = connection

export const wlanState: WlanState = {
  mode: 'ap',
  target_ssid: null,
  ip_address: null,
  last_attempt: null,
}

// ── Dive ────────────────────────────────────────────────────────────

// services/dive.py: PARAM_NAME = "DORIS_START"; _DORIS_STATE_NAMES maps
// -1 CONFIG, 0 MISSION_START, 1 DESCENT, 2 ON_BOTTOM, 3 ASCENT, 4 RECOVERY.
export const diveStatus: DiveStatus = {
  param: 'DORIS_START',
  value: 0,
  active: false,
  doris_script_state: -1,
  doris_script_state_name: 'CONFIG',
}

export const diveMission: DiveMissionState = {
  status: 'pending',
  configuration_name: 'Puget Sound Benthic Survey',
  loaded_at: minutesAgo(12),
  profile_id: 3,
  dive_file: 'dives/dive_20260727_0915.json',
}

export const diveHistory: DiveHistorySummary[] = [
  {
    id: 'dive_20260726_1432',
    name: 'Elliott Bay Transect 4',
    status: 'completed',
    date: daysAgo(1),
    duration: '1h 48m',
    location: '47.6019, -122.3782',
    start_location: '47.6019, -122.3782',
    end_location: '47.6031, -122.3765',
    max_depth: 62.4,
    estimated_depth_m: 60.0,
    log_max_depth_m: 62.4,
    mcap_relative_path: 'dives/dive_20260726_1432/telemetry.mcap',
    image_count: 214,
    video_count: 6,
    configuration: 'Puget Sound Benthic Survey',
  },
  {
    id: 'dive_20260724_0918',
    name: 'Blakely Rock Wall',
    status: 'completed',
    date: daysAgo(3),
    duration: '2h 12m',
    location: '47.5942, -122.4991',
    start_location: '47.5942, -122.4991',
    end_location: '47.5950, -122.4977',
    max_depth: 88.1,
    estimated_depth_m: 90.0,
    log_max_depth_m: 88.1,
    mcap_relative_path: 'dives/dive_20260724_0918/telemetry.mcap',
    image_count: 341,
    video_count: 11,
    configuration: 'Deep Wall Timelapse',
  },
  {
    id: 'dive_20260721_1105',
    name: 'Shakedown / Ballast Trim',
    status: 'aborted',
    date: daysAgo(6),
    duration: '0h 22m',
    location: '47.6205, -122.3493',
    start_location: '47.6205, -122.3493',
    end_location: '47.6205, -122.3493',
    max_depth: 11.3,
    estimated_depth_m: 30.0,
    log_max_depth_m: 11.3,
    mcap_relative_path: 'dives/dive_20260721_1105/telemetry.mcap',
    image_count: 18,
    video_count: 1,
    configuration: 'Shallow Test',
  },
]

export const missions: MissionSummary[] = diveHistory.map(d => ({
  id: d.id,
  name: d.name,
  status: d.status,
  date: d.date,
  duration: d.duration,
  location: d.location,
  max_depth: d.max_depth,
  image_count: d.image_count,
  video_count: d.video_count,
}))

export const armingStatus: ArmingStatus = {
  armed: false,
  armed_known: true,
  waiting_to_arm: false,
  reasons: [],
  checked_at: minutesAgo(0.1),
}

// ── Media ───────────────────────────────────────────────────────────

/**
 * Filenames follow ip_camera_recorder.py:
 *   snapshots  photos/radcam_<stamp>_<phase>_<seq:05d>.jpg
 *   finalized  radcam_<stamp>_<phase>_cyc<CC>_part<NN>_<NNNNN>_t<open>.mp4
 * where <stamp> is the dive-scoped %Y%m%d_%H%M%S and <open> is the
 * fragment's UTC open time as %Y%m%dt%H%M%S. The intermediate .ts
 * segments are deleted at finalize, so they never appear in the UI.
 */
function image(n: number, stamp: string, diveName: string, phase: string, minutes: number): MediaFile {
  const filename = `radcam_${stamp}_${phase}_${String(n).padStart(5, '0')}.jpg`
  const path = `dive_${stamp}/photos/${filename}`
  return {
    id: path,
    filename,
    media_type: 'image',
    size_bytes: 4_100_000 + n * 9_311,
    duration_seconds: null,
    resolution: '4K',
    created_at: minutesAgo(minutes + n * 0.5),
    mission_id: `dive_${stamp}`,
    dive_name: diveName,
    thumbnail_url: null,
    download_url: `/api/v1/media/download?path=${encodeURIComponent(path)}`,
    is_synced: n % 3 !== 0,
  }
}

function video(n: number, stamp: string, diveName: string, phase: string, opened: string, minutes: number): MediaFile {
  const filename =
    `radcam_${stamp}_${phase}_cyc01_part${String(n - 1).padStart(2, '0')}` +
    `_${String(n - 1).padStart(5, '0')}_t${opened}.mp4`
  const path = `dive_${stamp}/${filename}`
  return {
    id: path,
    filename,
    media_type: 'video',
    size_bytes: 182_000_000 + n * 1_400_000,
    duration_seconds: 300,
    resolution: '4K',
    created_at: minutesAgo(minutes + n * 5),
    mission_id: `dive_${stamp}`,
    dive_name: diveName,
    thumbnail_url: null,
    download_url: `/api/v1/media/download?path=${encodeURIComponent(path)}`,
    is_synced: n % 2 === 0,
  }
}

const DIVE_A = '20260726_143210'
const DIVE_B = '20260724_091845'

export const mediaFiles: MediaFile[] = [
  ...Array.from({ length: 8 }, (_, i) => image(i + 1, DIVE_A, 'Elliott Bay Transect 4', 'on_bottom', 1500)),
  ...Array.from({ length: 3 }, (_, i) => video(i + 1, DIVE_A, 'Elliott Bay Transect 4', 'on_bottom', '20260726t143512', 1500)),
  ...Array.from({ length: 6 }, (_, i) => image(i + 1, DIVE_B, 'Blakely Rock Wall', 'on_bottom', 4400)),
  ...Array.from({ length: 2 }, (_, i) => video(i + 1, DIVE_B, 'Blakely Rock Wall', 'descent', '20260724t092003', 4400)),
  {
    // finalize writes a manifest.json alongside the MP4s in the dive dir
    id: `dive_${DIVE_A}/manifest.json`,
    filename: 'manifest.json',
    media_type: 'data',
    size_bytes: 18_442,
    duration_seconds: null,
    resolution: null,
    created_at: minutesAgo(1400),
    mission_id: `dive_${DIVE_A}`,
    dive_name: 'Elliott Bay Transect 4',
    thumbnail_url: null,
    download_url: `/api/v1/media/download?path=${encodeURIComponent(`dive_${DIVE_A}/manifest.json`)}`,
    is_synced: true,
  },
]

export const mediaMissions: MediaMission[] = [
  {
    mission_id: 'dive_20260726_1432',
    mission_name: 'Elliott Bay Transect 4',
    date: daysAgo(1),
    image_count: 214,
    video_count: 6,
    data_file_count: 1,
    total_size_bytes: 2_140_000_000,
    thumbnail_url: null,
  },
  {
    mission_id: 'dive_20260724_0918',
    mission_name: 'Blakely Rock Wall',
    date: daysAgo(3),
    image_count: 341,
    video_count: 11,
    data_file_count: 1,
    total_size_bytes: 3_880_000_000,
    thumbnail_url: null,
  },
]

export const syncStatus: SyncStatus = {
  is_syncing: false,
  pending_files: 7,
  synced_files: 571,
  total_files: 578,
  last_sync: minutesAgo(95),
  error: null,
}

// ── Configurations ──────────────────────────────────────────────────

function configuration(name: string, diveName: string, depth: string, ageDays: number): DeploymentConfiguration {
  return {
    name,
    dive_name: diveName,
    estimated_depth: depth,
    // Phase defaults follow DescentPhase / BottomPhase / AscentPhase:
    // descent and ascent are off by default, bottom enables both.
    descent: {
      camera: cameraSettings(),
      light: lightSettings(),
    },
    bottom: {
      camera: cameraSettings({ enabled: true }),
      camera_delay: time('30'),
      light: lightSettings({ enabled: true }),
      light_delay: time('30'),
    },
    ascent: {
      same_as_descent: false,
      camera: cameraSettings(),
      light: lightSettings(),
      release_weight: {
        method: 'elapsed',
        elapsed: time('6', 'hours'),
        release_date: '2026-02-02',
        release_time: '12:00',
      },
    },
    recovery: {
      activate_mast_light: false,
      update_frequency: '5min',
      use_iridium: false,
      use_lora: false,
    },
    created_at: daysAgo(ageDays),
    updated_at: daysAgo(ageDays - 1),
  }
}

export const configurations: DeploymentConfiguration[] = [
  configuration('Puget Sound Benthic Survey', 'Elliott Bay Transect', '60 m', 14),
  configuration('Deep Wall Timelapse', 'Blakely Rock Wall', '90 m', 30),
  configuration('Shallow Test', 'Shakedown', '30 m', 45),
]

export const configurationSummaries: ConfigurationSummary[] = configurations.map(c => ({
  name: c.name,
  created_at: c.created_at,
  updated_at: c.updated_at,
}))

// ── Notifications ───────────────────────────────────────────────────

/**
 * Uses the exact templates from `NotificationService`, including the
 * `{key}-{hex8}` id format. Only notifications that service can actually
 * emit appear here — nothing fires on the SOFTWARE category, and the
 * storage warnings start at 80%, so a 27%-full disk produces none.
 */
export const notifications: NotificationItemApi[] = [
  {
    id: 'dive_completed-7c1a94e2',
    type: 'success',
    category: 'mission',
    title: 'Dive Completed',
    message: 'DORIS has completed the dive mission and returned to the surface.',
    timestamp: minutesAgo(1332),
    read: false,
    link_to: 'location',
  },
  {
    id: 'net_connected-3b8f0d51',
    type: 'info',
    category: 'network',
    title: 'Network Connected',
    message: 'Successfully connected to RV Falkor Deck.',
    timestamp: minutesAgo(240),
    read: false,
    link_to: null,
  },
  {
    id: 'dive_started-a04e7b6c',
    type: 'success',
    category: 'mission',
    title: 'Dive Started',
    message: 'DORIS has begun its dive mission. Monitoring in progress.',
    timestamp: minutesAgo(1440),
    read: true,
    link_to: null,
  },
  {
    id: 'net_disconnected-e59c2a17',
    type: 'warning',
    category: 'network',
    title: 'Network Disconnected',
    message: 'Lost connection to Dock Office.',
    timestamp: minutesAgo(2880),
    read: true,
    link_to: null,
  },
]

export const notificationSettings: NotificationSettingsApi = {
  mission_alerts: true,
  system_warnings: true,
  network_status: true,
  software_updates: false,
}

// ── Artemis ─────────────────────────────────────────────────────────

export const serialPorts: SerialPortInfo[] = [
  { device: '/dev/ttyUSB0', description: 'CP2102 USB to UART Bridge', hwid: 'USB VID:PID=10C4:EA60' },
  { device: '/dev/ttyACM0', description: 'SparkFun Artemis', hwid: 'USB VID:PID=1B4F:0026' },
]

// ── Tracker (SensorConfiguration.vue direct fetches) ────────────────

export const trackerGps = {
  latitude: 47.6019,
  longitude: -122.3782,
  altitude: 3.2,
  satellites: 9,
  fix: true,
  timestamp: minutesAgo(2),
}

export const trackerIridiumStatus = {
  messages: [
    { id: 1, timestamp: minutesAgo(180), direction: 'outbound', status: 'delivered', text: 'Position report' },
    { id: 2, timestamp: minutesAgo(60), direction: 'outbound', status: 'delivered', text: 'Position report' },
  ],
  last_id: 2,
  imei: '300434065264780',
  signal_quality: 4,
}
