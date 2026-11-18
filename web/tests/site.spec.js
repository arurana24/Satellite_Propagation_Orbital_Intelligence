// End-to-end tests of the built site with real Pyodide, NumPy, SciPy and plotly.js. Any console error,
// uncaught page error or failed request fails the test.
import { test, expect } from "@playwright/test";

// One browser context for the whole file: Pyodide + SciPy (~25 MB) download once, and the offline test
// reuses what the service worker cached during the earlier tests (exactly like a returning visitor).
test.describe.configure({ mode: "serial" });
let context, page, errors;
test.beforeAll(async ({ browser }) => {
  context = await browser.newContext({ baseURL: "http://localhost:4173", viewport: { width: 1400, height: 1000 }, acceptDownloads: true });
  page = await context.newPage();
  errors = watchErrors(page);
});
test.afterAll(async () => { await context?.close(); });
test.beforeEach(() => { errors.length = 0; });
import { readFileSync } from "node:fs";

const ENGINE_TIMEOUT = 240_000;

function watchErrors(page) {
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("requestfailed", (r) => {
    const why = r.failure()?.errorText || "";
    if (!why.includes("ERR_ABORTED")) errors.push(`requestfailed: ${r.url()} ${why}`);
  });
  return errors;
}

async function waitForDemo(page) {
  await expect(page.locator("#engine-bar")).toHaveClass(/ready/, { timeout: ENGINE_TIMEOUT });
  await expect(page.locator("body")).toHaveClass(/has-result/, { timeout: 60_000 });
}

// Every rendered figure must pass plotly.js's own schema validation (catches D1-type typos, which
// plotly.js would otherwise ignore silently).
// Plotly.validate in plotly.js 4.3.0 throws on any layout.legend (a plotly.js bug), so the legend object is
// checked separately against plotly.js's own schema and the rest goes through Plotly.validate.
async function validateAllPlots(page) {
  return page.evaluate(() => {
    const legendAttrs = window.Plotly.PlotSchema.get().layout.layoutAttributes.legend;
    return Object.entries(window.__oiFigures).map(([id, json]) => {
      const fig = JSON.parse(json);
      const { legend, ...layout } = fig.layout;
      const errors = (window.Plotly.validate(fig.data, layout) || []).map((e) => e.msg);
      for (const k of Object.keys(legend || {})) if (!(k in legendAttrs)) errors.push(`invalid layout.legend.${k}`);
      return { id, errors };
    }).filter((x) => x.errors.length);
  });
}

async function openSite() {
  if (!page.url().startsWith("http")) await page.goto("/");
  await waitForDemo(page);
}

test("demo mission runs end to end with no console errors", async () => {
  await openSite();                                           // the demo runs automatically on first load

  await expect(page.locator("#setup-metrics .metric")).toHaveCount(8);
  await expect(page.locator("#setup-metrics")).toContainText("97.80°");
  await expect(page.locator("#setup-findings .finding").first()).toBeVisible();
  await expect(page.locator("#f-hp")).toBeVisible();
  await expect(page.locator("#f-a")).toBeHidden();                    // only one orbit-definition mode at a time

  // explain panel on a number
  await page.locator('#setup-metrics [data-explain="period"]').click();
  await expect(page.locator("#explain")).toHaveClass(/open/);
  await expect(page.locator("#explain-body")).toContainText("T = 2π");
  await page.keyboard.press("Escape");

  // page 2: 3D orbit + ground track
  await page.click('a[data-page="orbit"]');
  await expect(page.locator("#plot-3d .main-svg").first()).toBeVisible();
  await expect(page.locator("#plot-gt .geo")).toHaveCount(1);
  await expect(page.locator("#plot-gt .coastlines").first()).toBeAttached();   // self-hosted map data loaded
  await expect(page.locator("#pass-tables table")).toHaveCount(2);
  await page.locator("#v-time").fill("1200");
  await expect(page.locator("#v-time-label")).toContainText("600.0 min");

  // page 3: comparison + validation
  await page.click('a[data-page="analyse"]');
  await expect(page.locator("#coverage-metrics")).toContainText("%");
  await page.click("#btn-compare");
  await expect(page.locator("#compare-metrics .metric")).toHaveCount(4, { timeout: 60_000 });
  await page.click("#btn-validate");
  await expect(page.locator("#validation-table tbody tr")).toHaveCount(8, { timeout: 60_000 });
  await expect(page.locator("#validation-table")).not.toContainText("FAIL");

  // Beginner/Expert toggle reveals the advanced sections
  await expect(page.locator("#plot-osc")).toBeHidden();
  await page.click('button[data-mode="expert"]');
  await expect(page.locator("#plot-osc")).toBeVisible();

  expect(await validateAllPlots(page)).toEqual([]);

  // page 4: exports
  await page.click('a[data-page="export"]');
  const [dlJson] = await Promise.all([page.waitForEvent("download"), page.click("#dl-json")]);
  const saved = JSON.parse(readFileSync(await dlJson.path(), "utf8"));
  expect(saved.config.orbit.i_deg).toBe(97.8);
  expect(saved.engine_version).toBeTruthy();
  const [dlPdf] = await Promise.all([page.waitForEvent("download"), page.click("#dl-pdf")]);
  expect(readFileSync(await dlPdf.path()).subarray(0, 5).toString()).toBe("%PDF-");
  const [dlCsv] = await Promise.all([page.waitForEvent("download"), page.click("#dl-csv")]);
  expect(readFileSync(await dlCsv.path(), "utf8").split("\n")[0]).toContain("x_km");

  expect(errors).toEqual([]);
});

test("launch to orbit: Sriharikota SSO flies south and is range-flagged", async () => {
  await openSite();
  await page.click('a[data-page="setup"]');
  await page.check("#f-launch-on");
  await page.fill("#f-dur", "3");
  await page.click("#btn-run");
  await expect(page.locator("#run-status")).toContainText("Done", { timeout: 180_000 });
  await page.click('a[data-page="orbit"]');
  await expect(page.locator("#launch-block")).toBeVisible();
  await expect(page.locator("#launch-metrics")).toContainText("south-going");
  await expect(page.locator("#launch-findings")).toContainText("outside the range-safety corridor");
  await expect(page.locator("#orbit-metrics")).toContainText("Launch hand-off");
  expect(await validateAllPlots(page)).toEqual([]);
  expect(errors).toEqual([]);
});

test("bad input shows a clear error instead of crashing", async () => {
  await openSite();
  await page.click('a[data-page="setup"]');
  await page.fill("#f-hp", "900");
  await page.fill("#f-ha", "500");
  await page.click("#btn-run");
  await expect(page.locator("#setup-error")).toContainText("Apogee altitude must be >= perigee altitude");
  await page.fill("#f-ha", "900");
  expect(errors).toEqual([]);
});

test("works offline after the first visit (Wi-Fi off)", async () => {
  await openSite();
  // wait until the service worker controls the page and has cached what the first visit loaded
  await page.waitForFunction(async () => {
    if (!navigator.serviceWorker.controller) return false;
    const keys = await (await caches.open("oi-v1")).keys();
    const urls = keys.map((r) => r.url);
    return urls.some((u) => u.includes("scipy")) && urls.some((u) => u.endsWith("/engine/engine.json")) && urls.some((u) => u.includes("world_110m"));
  }, null, { timeout: 60_000, polling: 1000 });

  await context.setOffline(true);
  errors.length = 0;
  await page.reload();
  await waitForDemo(page);
  await expect(page.locator("#setup-metrics")).toContainText("97.80°");
  await page.click('a[data-page="orbit"]');
  await expect(page.locator("#plot-gt .coastlines").first()).toBeAttached();
  expect(await page.evaluate(() => navigator.onLine)).toBe(false);
  expect(errors).toEqual([]);
  await context.setOffline(false);
});
