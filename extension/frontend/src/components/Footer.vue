<script setup lang="ts">
// Footer component - displays partner logos and BlueOS attribution
</script>

<template>
  <footer 
    class="border-t mt-auto"
    style="background-color: rgba(14, 36, 70, 0.9); backdrop-filter: blur(8px); border-color: rgba(65, 185, 195, 0.2)"
  >
    <div class="max-w-7xl mx-auto px-6 py-6 pb-20 md:pb-6">
      <div class="flex flex-col md:flex-row items-center justify-center gap-6 md:gap-16">
        <!-- Ocean Discovery League Logo -->
        <a 
          href="https://www.oceandiscoveryleague.org" 
          target="_blank" 
          rel="noopener noreferrer"
          class="flex items-center justify-center transition-opacity hover:opacity-80 flex-shrink-0"
        >
          <div 
            class="h-12 md:h-14 flex items-center justify-center px-4 rounded"
            style="color: #96EEF2; font-family: 'Montserrat', sans-serif; font-weight: 600"
          >
            Ocean Discovery League
          </div>
        </a>
        
        <!-- Blue Robotics Logo -->
        <a 
          href="https://bluerobotics.com/" 
          target="_blank" 
          rel="noopener noreferrer"
          class="flex items-center justify-center transition-opacity hover:opacity-80 flex-shrink-0"
        >
          <div 
            class="h-8 md:h-10 flex items-center justify-center px-4 rounded"
            style="color: #41B9C3; font-family: 'Montserrat', sans-serif; font-weight: 700"
          >
            Blue Robotics
          </div>
        </a>
      </div>
      
      <!-- BlueOS attribution -->
      <div class="text-center mt-6">
        <a 
          href="https://bluerobotics.com/what-is-blueos/" 
          target="_blank" 
          rel="noopener noreferrer"
          class="text-sm transition-colors hover:underline"
          style="color: #96EEF2"
        >
          Powered by BlueOS
        </a>
      </div>
    </div>
  </footer>
</template>

