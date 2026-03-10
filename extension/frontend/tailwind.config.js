/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        doris: {
          'light-cyan': '#96EEF2',
          'light-yellow': '#FFF8A7',
          'cyan': '#41B9C3',
          'yellow': '#FCD869',
          'teal': '#187D8B',
          'orange': '#FF9937',
          'dark-teal': '#004D64',
          'red': '#DD2C1D',
          'navy': '#0E2446',
        }
      },
      fontFamily: {
        'header': ['Montserrat', 'sans-serif'],
        'body': ['Helvetica', 'Arial', 'sans-serif'],
      }
    },
  },
  plugins: [],
}

