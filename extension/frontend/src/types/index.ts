export type Screen = 'home' | 'missions' | 'sensors' | 'media' | 'network' | 'notifications' | 'help' | 'location'

export interface Module {
  id: number
  name: string
  status: 'connected' | 'disconnected'
  moduleStatus: string
}

export interface Network {
  ssid: string
  signal: number
  frequency: '2.4GHz' | '5GHz'
  security: 'WPA2' | 'WPA3' | 'Open'
  saved: boolean
}

export interface SensorModule {
  id: number
  name: string
  type: 'camera' | 'sensor' | 'light'
  connected: boolean
  power: number
  sampleRate?: number
  calibrationFile?: string
  moduleStatus: string
}

export interface Mission {
  id: number
  name: string
  date: string
  duration: string
  location: string
  maxDepth: number
  images: number
  videos: number
  status: string
}

export interface MediaFile {
  id: number
  name: string
  type: 'video' | 'image' | 'sensor'
  mission: string
  date: string
  size: string
  timestamp: string
}

export interface MissionData {
  id: number
  name: string
  date: string
  files: number
  totalSize: string
  gpsPosition: string
  duration: string
}

export interface Notification {
  id: number
  type: 'success' | 'warning' | 'info'
  title: string
  message: string
  timestamp: string
  read: boolean
  linkTo?: Screen
}

export interface Tutorial {
  id: number
  title: string
  description: string
  duration: string
  category: 'setup' | 'operation' | 'troubleshooting'
  downloaded: boolean
}

export interface CameraSettings {
  resolution: string
  frameRate: number
  focus: string
}

export interface Duration {
  value: number
  unit: string
}

export interface Timelapse {
  enabled: boolean
  interval: number
}

