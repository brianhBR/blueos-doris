<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { Bell, CheckCircle, AlertTriangle, AlertCircle, Info, X } from 'lucide-vue-next'
import type { Screen } from '../types'
import { useNotifications } from '../composables/useApi'
import { parseBackendDateTime } from '../parseBackendTime'
import type { NotificationItemApi, NotificationSettingsApi } from '../composables/useApi'

const emit = defineEmits<{
  navigate: [screen: Screen]
}>()

const {
  notifications,
  unreadCount,
  settings,
  fetchNotifications,
  fetchSettings,
  markAsRead,
  markAllRead,
  deleteNotification,
  updateSettings,
} = useNotifications()

let pollId: number | undefined

onMounted(async () => {
  await Promise.all([fetchNotifications(), fetchSettings()])
  pollId = setInterval(fetchNotifications, 10000) as unknown as number
})

onUnmounted(() => {
  if (pollId) clearInterval(pollId)
})

const localSettings = computed(() => settings.value ?? {
  mission_alerts: true,
  system_warnings: true,
  network_status: true,
  software_updates: false,
})

async function toggleSetting(key: keyof NotificationSettingsApi) {
  const updated = { ...localSettings.value, [key]: !localSettings.value[key] }
  await updateSettings(updated)
}

const getIcon = (type: string) => {
  switch (type) {
    case 'success': return { component: CheckCircle, color: '#FCD869' }
    case 'warning': return { component: AlertTriangle, color: '#FF9937' }
    case 'error': return { component: AlertCircle, color: '#DD2C1D' }
    case 'info': return { component: Info, color: '#41B9C3' }
    default: return { component: Bell, color: '#96EEF2' }
  }
}

function formatTimestamp(ts: string): string {
  const date = parseBackendDateTime(ts)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return 'Just now'
  if (diffMin < 60) return `${diffMin} minute${diffMin === 1 ? '' : 's'} ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr} hour${diffHr === 1 ? '' : 's'} ago`
  const diffDay = Math.floor(diffHr / 24)
  return `${diffDay} day${diffDay === 1 ? '' : 's'} ago`
}

function handleNotificationClick(notification: NotificationItemApi) {
  if (notification.link_to) {
    emit('navigate', notification.link_to as Screen)
  }
}
</script>

<template>
  <div class="max-w-4xl mx-auto px-4 py-6 md:py-8">
    <div
      class="backdrop-blur-sm rounded-xl p-6 border mb-6"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <div class="flex items-center justify-between mb-6">
        <div class="flex items-center gap-3">
          <Bell class="w-6 h-6" style="color: #96EEF2" />
          <h1 class="text-white text-2xl">Notifications</h1>
          <span
            v-if="unreadCount > 0"
            class="px-3 py-1 rounded-full text-sm"
            style="background-color: #DD2C1D; color: white"
          >
            {{ unreadCount }} unread
          </span>
        </div>
        <button
          v-if="unreadCount > 0"
          @click="markAllRead()"
          class="px-4 py-2 rounded-lg transition-all text-sm"
          style="background-color: rgba(65, 185, 195, 0.2); color: #96EEF2; border: 1px solid rgba(65, 185, 195, 0.3)"
        >
          Mark all as read
        </button>
      </div>

      <div class="space-y-3">
        <div
          v-if="notifications.length === 0"
          class="text-center py-12"
        >
          <Bell class="w-16 h-16 mx-auto mb-4 opacity-30" style="color: #96EEF2" />
          <p style="color: #96EEF2">No notifications</p>
          <p class="text-sm mt-1" style="color: #96EEF2; opacity: 0.7">
            You're all caught up!
          </p>
        </div>

        <div
          v-for="notification in notifications"
          :key="notification.id"
          @click="handleNotificationClick(notification)"
          class="rounded-lg p-4 transition-all"
          :class="notification.link_to ? 'cursor-pointer hover:border-opacity-100' : ''"
          :style="{
            backgroundColor: notification.read
              ? 'rgba(14, 36, 70, 0.3)'
              : 'rgba(0, 77, 100, 0.5)',
            border: notification.read
              ? '1px solid rgba(65, 185, 195, 0.1)'
              : '1px solid rgba(65, 185, 195, 0.3)'
          }"
        >
          <div class="flex items-start gap-4">
            <div class="flex-shrink-0 mt-1">
              <component
                :is="getIcon(notification.type).component"
                class="w-5 h-5"
                :style="{ color: getIcon(notification.type).color }"
              />
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-start justify-between gap-2 mb-1">
                <h3 class="text-white">{{ notification.title }}</h3>
                <button
                  @click.stop="deleteNotification(notification.id)"
                  class="flex-shrink-0 p-1 rounded transition-colors opacity-50 hover:opacity-100"
                  style="color: #96EEF2"
                >
                  <X class="w-4 h-4" />
                </button>
              </div>
              <p class="text-sm mb-2" style="color: #96EEF2; opacity: 0.8">
                {{ notification.message }}
              </p>
              <div class="flex items-center justify-between">
                <span class="text-xs" style="color: #96EEF2; opacity: 0.6">
                  {{ formatTimestamp(notification.timestamp) }}
                </span>
                <button
                  v-if="!notification.read"
                  @click.stop="markAsRead(notification.id)"
                  class="text-xs px-3 py-1 rounded transition-all"
                  style="color: #41B9C3; border: 1px solid rgba(65, 185, 195, 0.3)"
                >
                  Mark as read
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Notification Settings -->
    <div
      class="backdrop-blur-sm rounded-xl p-6 border"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <h2 class="text-white mb-4">Notification Settings</h2>
      <div class="space-y-3">
        <div
          class="flex items-center justify-between p-4 rounded-lg"
          style="background-color: rgba(14, 36, 70, 0.5)"
        >
          <div>
            <p class="text-white">Mission Alerts</p>
            <p class="text-sm" style="color: #96EEF2; opacity: 0.7">
              Get notified when missions start, complete, or encounter issues
            </p>
          </div>
          <label class="toggle-switch">
            <input type="checkbox" :checked="localSettings.mission_alerts" @change="toggleSetting('mission_alerts')" />
            <span class="toggle-slider"></span>
          </label>
        </div>

        <div
          class="flex items-center justify-between p-4 rounded-lg"
          style="background-color: rgba(14, 36, 70, 0.5)"
        >
          <div>
            <p class="text-white">System Warnings</p>
            <p class="text-sm" style="color: #96EEF2; opacity: 0.7">
              Battery, storage, and hardware warnings
            </p>
          </div>
          <label class="toggle-switch">
            <input type="checkbox" :checked="localSettings.system_warnings" @change="toggleSetting('system_warnings')" />
            <span class="toggle-slider"></span>
          </label>
        </div>

        <div
          class="flex items-center justify-between p-4 rounded-lg"
          style="background-color: rgba(14, 36, 70, 0.5)"
        >
          <div>
            <p class="text-white">Network Status</p>
            <p class="text-sm" style="color: #96EEF2; opacity: 0.7">
              Connection and disconnection notifications
            </p>
          </div>
          <label class="toggle-switch">
            <input type="checkbox" :checked="localSettings.network_status" @change="toggleSetting('network_status')" />
            <span class="toggle-slider"></span>
          </label>
        </div>

        <div
          class="flex items-center justify-between p-4 rounded-lg"
          style="background-color: rgba(14, 36, 70, 0.5)"
        >
          <div>
            <p class="text-white">Software Updates</p>
            <p class="text-sm" style="color: #96EEF2; opacity: 0.7">
              Notify when new updates are available
            </p>
          </div>
          <label class="toggle-switch">
            <input type="checkbox" :checked="localSettings.software_updates" @change="toggleSetting('software_updates')" />
            <span class="toggle-slider"></span>
          </label>
        </div>
      </div>
    </div>
  </div>
</template>
