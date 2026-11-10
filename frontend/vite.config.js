import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';
export default defineConfig({plugins: [react()], build: {rollupOptions: {input: {main: fileURLToPath(new URL('./index.html', import.meta.url)), chatV3: fileURLToPath(new URL('./chat-v3.html', import.meta.url))}}}, server: {
  host: '0.0.0.0', port: 5173, strictPort: true, watch: {usePolling: true},
  proxy: {
    '/auth': {target: process.env.ADMIN_PROXY_TARGET || 'http://127.0.0.1:8090', changeOrigin:false},
    '/admin-api': {target: process.env.ADMIN_PROXY_TARGET || 'http://127.0.0.1:8090', changeOrigin:false},
    '/admin/login': {target: process.env.ADMIN_PROXY_TARGET || 'http://127.0.0.1:8090', changeOrigin:false},
    '/chat-v3-api': {target: process.env.CHATBOT_V3_PROXY_TARGET || 'http://127.0.0.1:8013',
      changeOrigin: true, rewrite: path => path.replace(/^\/chat-v3-api\//, '/api/')},
    '/api': {target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true},
    '/demo-api': {target: process.env.DEMO_API_PROXY_TARGET || 'http://127.0.0.1:8003',
      changeOrigin: true, rewrite: path => path.replace(/^\/demo-api\//, '/api/')},
  },
}});
