import { defineConfig, devices } from "@playwright/test";

/**
 * Where the app runs:
 * - `make dev` (default): Vite on :5173, which proxies /api to the API on :8000.
 * - `docker compose up`: E2E_BASE_URL=http://localhost:8080
 * - CI and production smoke tests set E2E_BASE_URL explicitly.
 *
 * Every test works in its own private sandbox workspace, so running the suite never disturbs the
 * shared demo. That makes it safe to point at the live Cloud Run URL after a deploy.
 */
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: "./tests",
  timeout: 10 * 60_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]], // `pnpm --filter @relay/e2e report` opens the last report
  use: {
    baseURL,
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    // CHROME_PATH lets you reuse an already installed Chromium instead of `playwright install`
    launchOptions: process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {},
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
