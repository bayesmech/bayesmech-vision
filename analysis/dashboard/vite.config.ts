import { fileURLToPath, URL } from 'node:url';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [svelte()],
  resolve: {
    alias: {
      $lib: fileURLToPath(new URL('./src/lib', import.meta.url)),
      $components: fileURLToPath(new URL('./src/components', import.meta.url)),
      $workers: fileURLToPath(new URL('./src/workers', import.meta.url))
    }
  },
  server: {
    fs: {
      allow: [
        fileURLToPath(new URL('.', import.meta.url)),
        fileURLToPath(new URL('../..', import.meta.url))
      ]
    },
    proxy: {
      '/streamlog': 'http://127.0.0.1:8080',
      '/api': 'http://127.0.0.1:8080',
      '/ws': {
        target: 'ws://127.0.0.1:8080',
        ws: true
      }
    }
  },
  build: {
    target: 'es2022'
  },
  worker: {
    format: 'es'
  }
});
