// Browser and accessibility tests for the KLEAR website. The server is the real site on a small fixture store,
// with a scripted stand-in for the model, so the tests need no network, data build or API key (see serve.py).
import {defineConfig, devices} from '@playwright/test';

const PORT = 8765;

export default defineConfig({
  testDir: './tests',
  outputDir: './test-results',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  // Answers run the whole agent pipeline on one local server that every worker shares, so allow time under load.
  expect: {timeout: 15_000},
  reporter: process.env.CI ? [['list'], ['html', {open: 'never'}], ['github']] : [['list'], ['html', {open: 'never'}]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {name: 'desktop', use: {...devices['Desktop Chrome'], viewport: {width: 1280, height: 800}}},
    {name: 'phone', use: {...devices['Pixel 7']}},
    // WebKit on a phone screen stands in for Safari on an iPhone.
    {name: 'phone-webkit', use: {...devices['iPhone 13']}},
  ],
  webServer: {
    command: `uv run python -m e2e.serve --port ${PORT}`,
    cwd: '..',
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: 'pipe',
  },
});
