import { defineConfig, devices } from "@playwright/test";

// Runs against the production build (vite preview) so the service worker and offline mode are exercised.
export default defineConfig({
  testDir: "./tests",
  timeout: 300_000,
  expect: { timeout: 30_000 },
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: { baseURL: "http://localhost:4173", ...devices["Desktop Chrome"], acceptDownloads: true,
         screenshot: "only-on-failure", trace: "retain-on-failure" },
  webServer: { command: "npm run preview", url: "http://localhost:4173", reuseExistingServer: !process.env.CI, timeout: 60_000 },
});
