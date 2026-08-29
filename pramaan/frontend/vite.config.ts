import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/** Dev and preview must proxy identically.
 *
 * `server.proxy` applies only to `vite dev`. `vite preview` — which serves the
 * production bundle and is therefore what you test a release with — ignores it
 * completely and needs its own `preview.proxy`. Without this the preview build
 * looked fine and every API call 404'd, which reads as "login is broken" rather
 * than "the proxy is missing". One shared object so the two cannot drift.
 */
const proxy = {
  // The API is served on 8000 in dev. Proxying keeps the frontend free of a
  // base-URL environment variable and keeps requests same-origin, so nothing
  // depends on CORS configuration that production would not have.
  // `/healthz` as well as `/api`: the rail reads the engine version and offline
  // flag from it, and without the proxy entry it silently rendered an ellipsis
  // forever.
  "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
  "/healthz": { target: "http://127.0.0.1:8000", changeOrigin: true },
};

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, proxy },
  preview: { host: "127.0.0.1", port: 4173, proxy },
});
