import { defineConfig } from "@playwright/test";

const python = process.env.SPECPILOT_PYTHON ?? ".venv/bin/python";

export default defineConfig({
  testDir: "../../tests/browser",
  use: { baseURL: "http://127.0.0.1:8765" },
  webServer: {
    command: `${python} -m uvicorn tests.browser.fixture_app:create_fixture_app --factory --host 127.0.0.1 --port 8765`,
    cwd: "../..",
    url: "http://127.0.0.1:8765/health",
    reuseExistingServer: false,
    env: {
      SPECPILOT_API_PROFILE: "fixture",
      SPECPILOT_API_BIND_HOST: "127.0.0.1",
      SPECPILOT_BROWSER_DSN: process.env.SPECPILOT_BROWSER_DSN ?? "",
    },
  },
});
