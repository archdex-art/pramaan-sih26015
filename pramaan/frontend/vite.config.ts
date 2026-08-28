import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    // The API is served on 8000 in dev. Proxying keeps the frontend free of a
    // base-URL environment variable and keeps requests same-origin, so nothing
    // depends on CORS configuration that production would not have.
    // `/healthz` as well as `/api`: the rail reads the engine version and
    // offline flag from it, and without the proxy entry it silently rendered
    // an ellipsis forever.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/healthz": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
