import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by FastAPI under /app in production (see app/main.py). In dev,
// `npm run dev` runs on :5173 and proxies the JSON API to uvicorn on :8000.
const apiPrefixes = [
  "/health",
  "/candidate",
  "/target-roles",
  "/sync",
  "/companies",
  "/sources",
  "/jobs",
  "/matches",
  "/operations",
  "/dashboard",
];

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(apiPrefixes.map((prefix) => [prefix, "http://127.0.0.1:8000"])),
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
