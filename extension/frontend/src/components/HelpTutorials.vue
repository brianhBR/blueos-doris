<script setup lang="ts">
import { ref, computed } from 'vue'
import { HelpCircle, PlayCircle, Book, AlertCircle, Send, Download, ExternalLink } from 'lucide-vue-next'
import type { Tutorial } from '../types'

const selectedCategory = ref<'all' | 'setup' | 'operation' | 'troubleshooting'>('all')
const bugReport = ref({ title: '', description: '', severity: 'medium' })

const tutorials = ref<Tutorial[]>([
  { id: 1, title: 'Getting Started with DORIS', description: 'Complete setup guide for first-time users', duration: '12:45', category: 'setup', downloaded: true },
  { id: 2, title: 'Network Configuration', description: 'How to connect DORIS to WiFi networks', duration: '8:30', category: 'setup', downloaded: true },
  { id: 3, title: 'Programming Your First Mission', description: 'Step-by-step mission planning tutorial', duration: '15:20', category: 'operation', downloaded: true },
  { id: 4, title: 'Sensor Calibration Guide', description: 'Calibrating CTD and other sensors', duration: '10:15', category: 'operation', downloaded: false },
  { id: 5, title: 'Troubleshooting Connection Issues', description: 'Common network problems and solutions', duration: '7:45', category: 'troubleshooting', downloaded: true },
  { id: 6, title: 'Data Export and Analysis', description: 'Working with captured data', duration: '14:00', category: 'operation', downloaded: false },
])

const faqs = [
  {
    question: 'How long does the battery last?',
    answer: 'Battery life depends on configuration, but typically provides 10-15 hours of continuous operation with standard settings.'
  },
  {
    question: 'What video formats are supported?',
    answer: 'DORIS natively supports H.265 and H.264 video formats. No transcoding is required for playback.'
  },
  {
    question: 'Can I use DORIS without internet connection?',
    answer: 'Yes! All mission programming, data access, and basic functionality work offline. Internet is only needed for software updates and cloud sync.'
  },
  {
    question: 'How do I update the firmware?',
    answer: 'Firmware updates are available through the software interface. Updates can be automatic or manually installed.'
  },
]

const filteredTutorials = computed(() => {
  if (selectedCategory.value === 'all') return tutorials.value
  return tutorials.value.filter(t => t.category === selectedCategory.value)
})

const handleSubmitBugReport = (e: Event) => {
  e.preventDefault()
  alert(`Bug report submitted:\nTitle: ${bugReport.value.title}\nSeverity: ${bugReport.value.severity}\n\nThank you for your feedback!`)
  bugReport.value = { title: '', description: '', severity: 'medium' }
}
</script>

<template>
  <div class="max-w-6xl mx-auto px-4 py-6 md:py-8">
    <div 
      class="backdrop-blur-sm rounded-xl p-6 border mb-6"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <h1 class="text-white text-2xl mb-2 flex items-center gap-2">
        <HelpCircle class="w-6 h-6" style="color: #96EEF2" />
        Help & Tutorials
      </h1>
      <p style="color: #96EEF2">Learn how to use DORIS effectively and troubleshoot common issues</p>
    </div>

    <!-- Quick Links -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
      <a
        href="#"
        class="backdrop-blur-sm rounded-xl p-6 border transition-all hover:border-opacity-60"
        style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
      >
        <Book class="w-8 h-8 mb-3" style="color: #96EEF2" />
        <h3 class="text-white mb-2">User Manual</h3>
        <p class="text-sm" style="color: #96EEF2">Complete documentation and reference guide</p>
      </a>
      <a
        href="#"
        class="backdrop-blur-sm rounded-xl p-6 border transition-all hover:border-opacity-60"
        style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
      >
        <ExternalLink class="w-8 h-8 mb-3" style="color: #96EEF2" />
        <h3 class="text-white mb-2">Online Community</h3>
        <p class="text-sm" style="color: #96EEF2">Connect with other DORIS users</p>
      </a>
      <a
        href="#"
        class="backdrop-blur-sm rounded-xl p-6 border transition-all hover:border-opacity-60"
        style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
      >
        <Download class="w-8 h-8 mb-3" style="color: #96EEF2" />
        <h3 class="text-white mb-2">Software Updates</h3>
        <p class="text-sm" style="color: #96EEF2">Check for latest firmware and features</p>
      </a>
    </div>

    <!-- Video Tutorials -->
    <div 
      class="backdrop-blur-sm rounded-xl p-6 border mb-6"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <h2 class="text-white text-xl mb-4">Video Tutorials</h2>

      <!-- Category Filter -->
      <div class="flex flex-wrap gap-2 mb-6">
        <button
          @click="selectedCategory = 'all'"
          class="px-4 py-2 rounded-lg transition-all"
          :style="selectedCategory === 'all' 
            ? { backgroundColor: '#41B9C3', color: 'white' }
            : { backgroundColor: 'rgba(14, 36, 70, 0.5)', color: '#96EEF2' }"
        >
          All Tutorials
        </button>
        <button
          @click="selectedCategory = 'setup'"
          class="px-4 py-2 rounded-lg transition-all"
          :style="selectedCategory === 'setup' 
            ? { backgroundColor: '#41B9C3', color: 'white' }
            : { backgroundColor: 'rgba(14, 36, 70, 0.5)', color: '#96EEF2' }"
        >
          Setup
        </button>
        <button
          @click="selectedCategory = 'operation'"
          class="px-4 py-2 rounded-lg transition-all"
          :style="selectedCategory === 'operation' 
            ? { backgroundColor: '#41B9C3', color: 'white' }
            : { backgroundColor: 'rgba(14, 36, 70, 0.5)', color: '#96EEF2' }"
        >
          Operation
        </button>
        <button
          @click="selectedCategory = 'troubleshooting'"
          class="px-4 py-2 rounded-lg transition-all"
          :style="selectedCategory === 'troubleshooting' 
            ? { backgroundColor: '#41B9C3', color: 'white' }
            : { backgroundColor: 'rgba(14, 36, 70, 0.5)', color: '#96EEF2' }"
        >
          Troubleshooting
        </button>
      </div>

      <!-- Tutorial List -->
      <div class="space-y-3">
        <div
          v-for="tutorial in filteredTutorials"
          :key="tutorial.id"
          class="rounded-lg p-4 flex items-center justify-between transition-all hover:bg-slate-700/30"
          style="background-color: rgba(14, 36, 70, 0.5)"
        >
          <div class="flex items-center gap-4 flex-1">
            <div 
              class="w-16 h-16 rounded-lg flex items-center justify-center"
              style="background: linear-gradient(135deg, #41B9C3 0%, #187D8B 100%)"
            >
              <PlayCircle class="w-8 h-8 text-white" />
            </div>
            <div class="flex-1">
              <h3 class="text-white mb-1">{{ tutorial.title }}</h3>
              <p class="text-sm mb-1" style="color: #96EEF2">{{ tutorial.description }}</p>
              <div class="flex items-center gap-3 text-sm">
                <span style="color: #41B9C3">{{ tutorial.duration }}</span>
                <span 
                  class="px-2 py-0.5 rounded text-xs"
                  :style="tutorial.downloaded 
                    ? { backgroundColor: 'rgba(34, 197, 94, 0.2)', color: '#22c55e' }
                    : { backgroundColor: 'rgba(59, 130, 246, 0.2)', color: '#3b82f6' }"
                >
                  {{ tutorial.downloaded ? 'Downloaded' : 'Online Only' }}
                </span>
                <span 
                  class="px-2 py-0.5 rounded text-xs capitalize"
                  style="background-color: rgba(168, 85, 247, 0.2); color: #a855f7"
                >
                  {{ tutorial.category }}
                </span>
              </div>
            </div>
          </div>
          <div class="flex gap-2">
            <button 
              v-if="!tutorial.downloaded"
              class="px-4 py-2 rounded-lg transition-all flex items-center gap-2"
              style="background-color: rgba(14, 36, 70, 0.5); color: white"
            >
              <Download class="w-4 h-4" />
              Download
            </button>
            <button 
              class="px-4 py-2 text-white rounded-lg transition-all hover:opacity-90"
              style="background-color: #41B9C3"
            >
              Watch
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- FAQ Section -->
    <div 
      class="backdrop-blur-sm rounded-xl p-6 border mb-6"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <h2 class="text-white text-xl mb-4">Frequently Asked Questions</h2>
      <div class="space-y-4">
        <div 
          v-for="(faq, index) in faqs" 
          :key="index"
          class="rounded-lg p-4"
          style="background-color: rgba(14, 36, 70, 0.3)"
        >
          <h3 class="text-white mb-2">{{ faq.question }}</h3>
          <p class="text-sm" style="color: #96EEF2">{{ faq.answer }}</p>
        </div>
      </div>
    </div>

    <!-- Bug Report Form -->
    <div 
      class="backdrop-blur-sm rounded-xl p-6 border"
      style="background-color: rgba(0, 77, 100, 0.4); border-color: rgba(65, 185, 195, 0.3)"
    >
      <h2 class="text-white text-xl mb-4 flex items-center gap-2">
        <AlertCircle class="w-6 h-6" style="color: #96EEF2" />
        Report a Bug or Issue
      </h2>
      <form @submit="handleSubmitBugReport" class="space-y-4">
        <div>
          <label class="block text-sm mb-2" style="color: #96EEF2">Issue Title</label>
          <input
            type="text"
            v-model="bugReport.title"
            required
            class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
            placeholder="Brief description of the issue"
          />
        </div>
        <div>
          <label class="block text-sm mb-2" style="color: #96EEF2">Severity</label>
          <select
            v-model="bugReport.severity"
            class="w-full px-4 py-2 text-white rounded-lg focus:outline-none"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
          >
            <option value="low">Low - Minor inconvenience</option>
            <option value="medium">Medium - Affects functionality</option>
            <option value="high">High - Critical issue</option>
          </select>
        </div>
        <div>
          <label class="block text-sm mb-2" style="color: #96EEF2">Detailed Description</label>
          <textarea
            v-model="bugReport.description"
            required
            rows="5"
            class="w-full px-4 py-2 text-white rounded-lg focus:outline-none resize-none"
            style="background-color: rgba(14, 36, 70, 0.5); border: 1px solid rgba(65, 185, 195, 0.3)"
            placeholder="Please provide as much detail as possible, including steps to reproduce the issue..."
          ></textarea>
        </div>
        <button
          type="submit"
          class="w-full px-4 py-3 text-white rounded-lg transition-all hover:opacity-90 flex items-center justify-center gap-2"
          style="background-color: #41B9C3"
        >
          <Send class="w-5 h-5" />
          Submit Bug Report
        </button>
      </form>
    </div>
  </div>
</template>

