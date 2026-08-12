import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "../../tests/browser",
  use: { baseURL: "http://127.0.0.1:8765" },
  webServer: {
    command: ".venv/bin/python -m uvicorn specpilot.api.runtime:create_runtime_app --factory --host 127.0.0.1 --port 8765",
    cwd: "../..",
    url: "http://127.0.0.1:8765/health",
    reuseExistingServer: false,
    env: {
      SPECPILOT_API_PROFILE: "fixture",
      SPECPILOT_API_BIND_HOST: "127.0.0.1",
    },
  },
});
