/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The browser only ever talks to this origin; /api/* is forwarded to FastAPI,
// so the API needs no CORS setup (docs/ARCHITECTURE.md §9).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5181,
    strictPort: true,
    proxy: {
      "/api": {
        // 127.0.0.1, not localhost: Node resolves localhost to IPv6 ::1, uvicorn listens on IPv4
        target: process.env.API_URL ?? "http://127.0.0.1:8030",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
