// Dedicated harness: real HospitalWorkspace + real API, synthetic in-memory DB.
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = fileURLToPath(new URL('..', import.meta.url));
const server = await createServer({
  root, configFile: false, plugins: [react(), {
    name: 'hsp01-uat-entry', configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (req.url !== '/') return next();
        const html = await server.transformIndexHtml('/', '<!doctype html><html><head><title>HSP01 Synthetic UI Test</title></head><body><div id="root"></div><script type="module" src="/src/hospital-uat/main.tsx"></script></body></html>');
        res.setHeader('Content-Type', 'text/html'); res.end(html);
      });
    },
  }],
  resolve: { alias: { '@': path.join(root, 'src') } },
  define: { 'process.env.NEXT_PUBLIC_BACKEND_URL': '""' },
  server: { host: '127.0.0.1', port: 15173, strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:18001', '/uat': 'http://127.0.0.1:18001' } },
});
await server.listen();
server.printUrls();
