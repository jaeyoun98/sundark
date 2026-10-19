const { defineConfig } = require("@playwright/test");
module.exports = defineConfig({
  testDir: "./tests/e2e",
  testMatch: "*.spec.js",
  workers: 1,
  fullyParallel: false,
  timeout: 60000,
  use: {
    baseURL: "http://127.0.0.1:18743",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command:
      "uv run --no-sync python -m uvicorn tests.e2e.server:app --host 127.0.0.1 --port 18743",
    url: "http://127.0.0.1:18743/api/config",
    reuseExistingServer: false,
    timeout: 120000,
  },
});
