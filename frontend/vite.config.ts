import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // The API runs as its own single-worker process (S8: Qdrant takes an
    // exclusive file lock, so it cannot be forked). Proxying keeps the browser
    // on one origin and avoids CORS in development.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
