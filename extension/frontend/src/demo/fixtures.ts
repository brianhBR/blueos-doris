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

function cameraSettings(overrides: Partial<CameraSettings> = {}): CameraSettings {
  return {
    enabled: true,
    camera_type: 'timelapse',
    capture_frequency: 30,
    capture_frequency_unit: 'seconds',
    video_record: time('30'),
    video_pause: time('5', 'minutes'),
    timelapse_light_pre: time('2'),
    timelapse_light_post: time('1'),
    resolution: '4056x3040',
    image_type: 'still',
    file_format: 'jpeg',
    video_file_format: 'mp4',
    frame_rate: 30,
    focus: 'auto',
    iso: '400',
    white_balance: 'underwater',
    exposure: 'auto',
    sharpness: 'medium',
    sleep_timer_enabled: false,
    sleep_timer: time('10', 'minutes'),
    ...overrides,
  }
}

function lightSettings(overrides: Partial<LightSettings> = {}): LightSettings {
  return {
    enabled: true,
    mode: 'interval',
    brightness: 65,
    match_camera_interval: true,
    on_time: time('3'),
    off_time: time('27'),
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

export const sensorModules: SensorModule[] = [
  {
    id: 'ctd',
    name: 'CTD (Conductivity/Temp/Depth)',
    type: 'ctd',
    status: 'connected',
    module_status: 'Sampling at 1 Hz',
    last_reading: minutesAgo(0.1),
    power_usage: 0.8,
    sample_rate: 1,
    firmware_version: '2.4.1',
  },
  {
    id: 'camera',
    name: 'Imaging Camera',
    type: 'camera',
    status: 'connected',
    module_status: 'Idle — armed for next dive',
    last_reading: minutesAgo(3),
    power_usage: 2.1,
    sample_rate: null,
    firmware_version: '1.9.0',
  },
  {
    id: 'lights',
    name: 'Lumen Light Array',
    type: 'light',
    status: 'connected',
    module_status: 'Standby at 0%',
    last_reading: minutesAgo(3),
    power_usage: 0.1,
    sample_rate: null,
    firmware_version: '1.2.3',
  },
  {
    id: 'artemis',
    name: 'Artemis Surface Tracker',
    type: 'tracker',
    status: 'connected',
    module_status: 'GPS lock, Iridium registered',
    last_reading: minutesAgo(1),
    power_usage: 0.5,
    sample_rate: null,
    firmware_version: '0.8.2',
  },
  {
    id: 'imu',
    name: 'Inertial Measurement Unit',
    type: 'imu',
    status: 'connected',
    module_status: 'Calibrated',
    last_reading: minutesAgo(0.05),
    power_usage: 0.2,
    sample_rate: 50,
    firmware_version: '4.5.7',
  },
]

export const sensorReadings: Record<string, SensorReading[]> = {
  ctd: [
    { sensor_id: 'ctd', sensor_name: 'Temperature', value: 11.42, unit: '°C', timestamp: minutesAgo(0.1), quality: 98 },
    { sensor_id: 'ctd', sensor_name: 'Salinity', value: 30.18, unit: 'PSU', timestamp: minutesAgo(0.1), quality: 97 },
    { sensor_id: 'ctd', sensor_name: 'Depth', value: 0.0, unit: 'm', timestamp: minutesAgo(0.1), quality: 99 },
  ],
  imu: [
    { sensor_id: 'imu', sensor_name: 'Roll', value: 1.8, unit: '°', timestamp: minutesAgo(0.05), quality: 100 },
    { sensor_id: 'imu', sensor_name: 'Pitch', value: -0.6, unit: '°', timestamp: minutesAgo(0.05), quality: 100 },
  ],
}

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

export const diveStatus: DiveStatus = {
  param: 'DORIS_DIVE_STATE',
  value: 0,
  active: false,
  doris_script_state: 0,
  doris_script_state_name: 'IDLE',
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

function image(n: number, dive: string, diveName: string, minutes: number): MediaFile {
  return {
    id: `img_${dive}_${n}`,
    filename: `IMG_${String(n).padStart(4, '0')}.jpg`,
    media_type: 'image',
    size_bytes: 4_100_000 + n * 9_311,
    duration_seconds: null,
    resolution: '4056x3040',
    created_at: minutesAgo(minutes + n * 0.5),
    mission_id: dive,
    dive_name: diveName,
    thumbnail_url: null,
    download_url: `/api/v1/media/download?path=${dive}/IMG_${String(n).padStart(4, '0')}.jpg`,
    is_synced: n % 3 !== 0,
  }
}

function video(n: number, dive: string, diveName: string, minutes: number): MediaFile {
  return {
    id: `vid_${dive}_${n}`,
    filename: `VID_${String(n).padStart(4, '0')}.mp4`,
    media_type: 'video',
    size_bytes: 182_000_000 + n * 1_400_000,
    duration_seconds: 30,
    resolution: '1920x1080',
    created_at: minutesAgo(minutes + n * 4),
    mission_id: dive,
    dive_name: diveName,
    thumbnail_url: null,
    download_url: `/api/v1/media/download?path=${dive}/VID_${String(n).padStart(4, '0')}.mp4`,
    is_synced: n % 2 === 0,
  }
}

export const mediaFiles: MediaFile[] = [
  ...Array.from({ length: 8 }, (_, i) => image(i + 1, 'dive_20260726_1432', 'Elliott Bay Transect 4', 1500)),
  ...Array.from({ length: 3 }, (_, i) => video(i + 1, 'dive_20260726_1432', 'Elliott Bay Transect 4', 1500)),
  ...Array.from({ length: 6 }, (_, i) => image(i + 1, 'dive_20260724_0918', 'Blakely Rock Wall', 4400)),
  ...Array.from({ length: 2 }, (_, i) => video(i + 1, 'dive_20260724_0918', 'Blakely Rock Wall', 4400)),
  {
    id: 'mcap_20260726',
    filename: 'telemetry.mcap',
    media_type: 'data',
    size_bytes: 24_800_000,
    duration_seconds: null,
    resolution: null,
    created_at: minutesAgo(1500),
    mission_id: 'dive_20260726_1432',
    dive_name: 'Elliott Bay Transect 4',
    thumbnail_url: null,
    download_url: '/api/v1/media/download?path=dive_20260726_1432/telemetry.mcap',
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
    descent: {
      camera: cameraSettings({ camera_type: 'timelapse', capture_frequency: 60 }),
      light: lightSettings({ brightness: 40 }),
    },
    bottom: {
      camera: cameraSettings({ camera_type: 'video-interval' }),
      camera_delay: time('2', 'minutes'),
      light: lightSettings({ brightness: 80 }),
      light_delay: time('2', 'minutes'),
    },
    ascent: {
      same_as_descent: true,
      camera: cameraSettings({ enabled: false }),
      light: lightSettings({ enabled: false }),
      release_weight: {
        method: 'elapsed',
        elapsed: time('90', 'minutes'),
        release_date: new Date(BOOT).toISOString().slice(0, 10),
        release_time: '14:30',
      },
    },
    recovery: {
      activate_mast_light: true,
      update_frequency: '5 minutes',
      use_iridium: true,
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

export const notifications: NotificationItemApi[] = [
  {
    id: 'n1',
    type: 'success',
    category: 'mission',
    title: 'Dive completed',
    message: 'Elliott Bay Transect 4 finished successfully. 214 images and 6 videos captured.',
    timestamp: minutesAgo(1440),
    read: false,
    link_to: 'alldives',
  },
  {
    id: 'n2',
    type: 'warning',
    category: 'system',
    title: 'Storage above 25%',
    message: 'External SSD is 27% full. Consider offloading media before the next deployment.',
    timestamp: minutesAgo(300),
    read: false,
    link_to: 'media',
  },
  {
    id: 'n3',
    type: 'info',
    category: 'network',
    title: 'Iridium check-in received',
    message: 'Surface tracker reported position 47.6019, -122.3782.',
    timestamp: minutesAgo(180),
    read: true,
    link_to: null,
  },
  {
    id: 'n4',
    type: 'info',
    category: 'software',
    title: 'Extension updated',
    message: 'DORIS extension updated to bh-1.4.0.',
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
