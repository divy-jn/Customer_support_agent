import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E configuration for Customer Support Agent UI.
 *
 * Runs isolated local backend (port 8002) and Next.js frontend (port 3003).
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 35000,
  expect: {
    timeout: 10000,
  },
  fullyParallel: false,
  retries: 0,
  workers: 1, // run serially to prevent session and port collisions
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:3003',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'mobile-chrome',
      use: { ...devices['Pixel 5'] },
    },
  ],
  webServer: [
    {
      command: 'python -m tests.e2e_test_server',
      cwd: '../backend',
      url: 'http://127.0.0.1:8002/health',
      reuseExistingServer: !process.env.CI,
      timeout: 45000,
    },
    {
      command: 'npm run dev',
      url: 'http://localhost:3003',
      reuseExistingServer: !process.env.CI,
      timeout: 60000,
    },
  ],
});
