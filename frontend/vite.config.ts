import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // The API runs as its own single-worker process (S8: Qdrant takes an
    // exclusive file lock, so it cannot be forked). Proxying keeps the browser
    // on one origin and avoids CORS in development.
    proxy: {
      // Overridable so the dev frontend can be pointed at a second backend —
      // needed whenever one is already holding :8000 (and the Qdrant lock with
      // it), which is the normal state while a generation is running.
      "/api": {
        target: process.env.PREPIFY_API ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
