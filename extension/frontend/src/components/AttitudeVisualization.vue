<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js'
import { Wifi, WifiOff } from 'lucide-vue-next'
import { useAttitudeWs } from '../composables/useAttitudeWs'

const MODEL_PATH = '/models/doris.glb'
const LERP = 0.15

const { attitude, connected } = useAttitudeWs()
const canvasContainer = ref<HTMLDivElement | null>(null)

let scene: THREE.Scene
let camera: THREE.PerspectiveCamera
let renderer: THREE.WebGLRenderer
let controls: OrbitControls
let vehicleModel: THREE.Object3D | null = null
let animationId: number

const smooth = { roll: 0, pitch: 0, yaw: 0 }

function initScene() {
  if (!canvasContainer.value) return
  const w = canvasContainer.value.clientWidth
  const h = canvasContainer.value.clientHeight

  scene = new THREE.Scene()
  scene.background = new THREE.Color(0x0a1628)
  scene.fog = new THREE.Fog(0x0a1628, 15, 30)

  camera = new THREE.PerspectiveCamera(50, w / h, 0.1, 100)
  camera.position.set(4, 3, 4)
  camera.lookAt(0, 0, 0)

  renderer = new THREE.WebGLRenderer({ antialias: true })
  renderer.setSize(w, h)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  canvasContainer.value.appendChild(renderer.domElement)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.05
  controls.minDistance = 2
  controls.maxDistance = 15
  controls.enablePan = false

  scene.add(new THREE.AmbientLight(0x404060, 0.8))
  const sun = new THREE.DirectionalLight(0xffffff, 1.2)
  sun.position.set(5, 8, 5)
  scene.add(sun)
  const fill = new THREE.DirectionalLight(0x41B9C3, 0.3)
  fill.position.set(-3, 2, -3)
  scene.add(fill)

  const draco = new DRACOLoader()
  draco.setDecoderPath('/draco/')
  const loader = new GLTFLoader()
  loader.setDRACOLoader(draco)

  loader.load(MODEL_PATH, (gltf) => {
    const model = gltf.scene
    const box = new THREE.Box3().setFromObject(model)
    const size = box.getSize(new THREE.Vector3())
    const scale = 3 / Math.max(size.x, size.y, size.z)
    model.scale.setScalar(scale)
    const center = box.getCenter(new THREE.Vector3())
    model.position.set(-center.x * scale, -center.y * scale, -center.z * scale)
    vehicleModel = model
    scene.add(vehicleModel)
  })

  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(2.5, 0.012, 8, 128),
    new THREE.MeshBasicMaterial({ color: 0x41B9C3, transparent: true, opacity: 0.25 }),
  )
  ring.rotation.x = Math.PI / 2
  scene.add(ring)
  scene.add(new THREE.GridHelper(6, 12, 0x1a3a5c, 0x0d2035))

  animate()
}

function animate() {
  animationId = requestAnimationFrame(animate)

  if (attitude.value && vehicleModel) {
    smooth.roll += (attitude.value.roll_rad - smooth.roll) * LERP
    smooth.pitch += (attitude.value.pitch_rad - smooth.pitch) * LERP
    smooth.yaw += (attitude.value.yaw_rad - smooth.yaw) * LERP
    // Aerospace attitude is a yaw->pitch->roll (3-2-1) intrinsic sequence.
    // With Three.js Y-up, that maps to yaw about Y, pitch about X, roll about Z,
    // which requires Euler order 'YXZ'. Any other order couples the axes so that
    // e.g. a pure yaw appears as roll once pitch/roll are non-zero.
    vehicleModel.rotation.set(-smooth.pitch, -smooth.yaw, smooth.roll, 'YXZ')
  }

  controls.update()
  renderer.render(scene, camera)
}

function handleResize() {
  if (!canvasContainer.value || !camera || !renderer) return
  const w = canvasContainer.value.clientWidth
  const h = canvasContainer.value.clientHeight
  camera.aspect = w / h
  camera.updateProjectionMatrix()
  renderer.setSize(w, h)
}

function formatValue(value: number): string {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}`
}

onMounted(() => {
  initScene()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  if (animationId) cancelAnimationFrame(animationId)
  if (renderer) {
    renderer.dispose()
    renderer.domElement.remove()
  }
  if (controls) controls.dispose()
})
</script>

<template>
  <div class="relative w-full h-full">
    <div ref="canvasContainer" class="w-full h-full" />

    <!-- Connection badge -->
    <div
      class="absolute top-2 right-2 flex items-center gap-1 px-2 py-1 rounded-full text-xs"
      :style="{
        backgroundColor: connected ? 'rgba(0, 212, 170, 0.2)' : 'rgba(255, 71, 87, 0.2)',
        color: connected ? '#00D4AA' : '#FF4757',
      }"
    >
      <Wifi v-if="connected" class="w-3 h-3" />
      <WifiOff v-else class="w-3 h-3" />
      {{ connected ? 'Live' : 'Off' }}
    </div>

    <!-- Attitude readout -->
    <div class="absolute bottom-2 left-2 right-2 flex justify-center gap-1.5">
      <div
        v-for="axis in [
          { label: 'R', value: attitude?.roll_deg ?? 0, color: '#00D4AA' },
          { label: 'P', value: attitude?.pitch_deg ?? 0, color: '#41B9C3' },
          { label: 'Y', value: attitude?.yaw_deg ?? 0, color: '#FF8C42' },
        ]"
        :key="axis.label"
        class="px-2 py-1 rounded text-center flex-1"
        style="background-color: rgba(14, 36, 70, 0.88); backdrop-filter: blur(8px)"
      >
        <div class="text-[10px] uppercase tracking-wider" :style="{ color: axis.color }">
          {{ axis.label }}
        </div>
        <div class="text-sm font-mono font-bold text-white leading-tight">
          {{ formatValue(axis.value) }}°
        </div>
      </div>
    </div>
  </div>
</template>
