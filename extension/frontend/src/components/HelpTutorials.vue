<script setup lang="ts">
import { HelpCircle, PlayCircle, Book, AlertCircle, Download, ExternalLink } from 'lucide-vue-next'

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
</script>

<template>
  <div class="max-w-6xl mx-auto px-4 py-6 md:py-8">
    <!-- Header -->
    <div class="bg-slate-800/50 backdrop-blur-sm rounded-xl p-6 border border-cyan-500/20 mb-6">
      <h1 class="text-white text-2xl mb-2 flex items-center gap-2">
        <HelpCircle class="w-6 h-6 text-cyan-400" />
        Help & Tutorials
      </h1>
      <p class="text-cyan-300">Learn how to use DORIS effectively and troubleshoot common issues</p>
    </div>

    <!-- Quick Links -->
    <div class="relative overflow-hidden rounded-xl mb-6">
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4 opacity-40 pointer-events-none select-none">
        <div class="bg-slate-800/50 backdrop-blur-sm rounded-xl p-6 border border-cyan-500/20">
          <Book class="w-8 h-8 text-cyan-400 mb-3" />
          <h3 class="text-white mb-2">User Manual</h3>
          <p class="text-cyan-300 text-sm">Complete documentation and reference guide</p>
        </div>
        <div class="bg-slate-800/50 backdrop-blur-sm rounded-xl p-6 border border-cyan-500/20">
          <ExternalLink class="w-8 h-8 text-cyan-400 mb-3" />
          <h3 class="text-white mb-2">Online Community</h3>
          <p class="text-cyan-300 text-sm">Connect with other DORIS users</p>
        </div>
        <div class="bg-slate-800/50 backdrop-blur-sm rounded-xl p-6 border border-cyan-500/20">
          <Download class="w-8 h-8 text-cyan-400 mb-3" />
          <h3 class="text-white mb-2">Software Updates</h3>
          <p class="text-cyan-300 text-sm">Check for latest firmware and features</p>
        </div>
      </div>
      <div class="absolute inset-0 flex items-center justify-center">
        <span class="px-4 py-1.5 rounded-full bg-cyan-500/20 border border-cyan-400/40 text-cyan-200 text-sm font-medium tracking-wide uppercase">
          Coming Soon
        </span>
      </div>
    </div>

    <!-- Video Tutorials -->
    <div class="bg-slate-800/50 backdrop-blur-sm rounded-xl p-6 border border-cyan-500/20 mb-6">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-white text-xl">Video Tutorials</h2>
        <span class="px-3 py-1 rounded-full bg-cyan-500/20 border border-cyan-400/40 text-cyan-200 text-xs font-medium tracking-wide uppercase">
          Coming Soon
        </span>
      </div>
      <div class="flex flex-col items-center justify-center text-center py-12 px-4 rounded-lg border border-dashed border-cyan-500/30 bg-slate-700/20">
        <PlayCircle class="w-12 h-12 text-cyan-400/60 mb-3" />
        <h3 class="text-white mb-1">Tutorials are on the way</h3>
        <p class="text-cyan-300 text-sm max-w-md">
          Step-by-step setup, operation, and troubleshooting videos will appear here once they're produced.
        </p>
      </div>
    </div>

    <!-- FAQ Section -->
    <div class="bg-slate-800/50 backdrop-blur-sm rounded-xl p-6 border border-cyan-500/20 mb-6">
      <h2 class="text-white text-xl mb-4">Frequently Asked Questions</h2>
      <div class="space-y-4">
        <div
          v-for="(faq, index) in faqs"
          :key="index"
          class="bg-slate-700/30 rounded-lg p-4"
        >
          <h3 class="text-white mb-2">{{ faq.question }}</h3>
          <p class="text-cyan-300 text-sm">{{ faq.answer }}</p>
        </div>
      </div>
    </div>

    <!-- Bug Report Form -->
    <div class="relative overflow-hidden bg-slate-800/50 backdrop-blur-sm rounded-xl p-6 border border-cyan-500/20">
      <h2 class="text-white text-xl mb-4 flex items-center gap-2">
        <AlertCircle class="w-6 h-6 text-cyan-400" />
        Report a Bug or Issue
      </h2>
      <div class="flex flex-col items-center justify-center text-center py-10 px-4 rounded-lg border border-dashed border-cyan-500/30 bg-slate-700/20">
        <span class="px-3 py-1 mb-3 rounded-full bg-cyan-500/20 border border-cyan-400/40 text-cyan-200 text-xs font-medium tracking-wide uppercase">
          Coming Soon
        </span>
        <p class="text-cyan-300 text-sm max-w-md">
          In-app bug reporting isn't wired up yet. For now, please file issues on the
          <a
            href="https://github.com/bluerobotics/blueos-doris/issues"
            target="_blank"
            rel="noopener noreferrer"
            class="text-cyan-400 underline hover:text-cyan-300"
          >project's GitHub page</a>.
        </p>
      </div>
    </div>
  </div>
</template>
