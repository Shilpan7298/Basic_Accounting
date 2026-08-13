export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: '#111827', soft: '#374151', mute: '#6b7280' },
        brand: { 50: '#eef4ff', 100: '#d9e6ff', 500: '#2563eb', 600: '#1d4ed8', 700: '#1e40af' },
      },
    },
  },
  plugins: [],
}
