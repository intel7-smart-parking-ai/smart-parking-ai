import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins: [react()], server: {
  host: '0.0.0.0', port: 5173, strictPort: true, watch: {usePolling: true},
  proxy: {
    '/auth': {target: process.env.ADMIN_PROXY_TARGET || 'http://127.0.0.1:8090', changeOrigin:false},
    '/admin-api': {target: process.env.ADMIN_PROXY_TARGET || 'http://127.0.0.1:8090', changeOrigin:false},
    '/admin/login': {target: process.env.ADMIN_PROXY_TARGET || 'http://127.0.0.1:8090', changeOrigin:false},
    '/api': {target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true},
    '/demo-api': {target: process.env.DEMO_API_PROXY_TARGET || 'http://127.0.0.1:8003',
      changeOrigin: true, rewrite: path => path.replace(/^\/demo-api\//, '/api/')},
  },
}});
