import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  base: "/trace/",
  build: {
    outDir: resolve(__dirname, "../../src/specpilot/api/static/trace"),
    emptyOutDir: true,
  },
  test: { environment: "jsdom" },
});
