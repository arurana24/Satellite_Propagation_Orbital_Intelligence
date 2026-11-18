# Orbital Intelligence

**Launch it. Orbit it. Understand it.** Satellite mission design that runs entirely in your browser: simulate a
rocket ascent, hand the burnout state to an orbit propagator (two-body + J2), then see the ground track, passes and
coverage, with explainable rule-based feasibility checks.

`Launch site → ascent → orbit insertion → propagation → ground track → passes & coverage → feasibility verdict`

The physics engine is Python (NumPy/SciPy). The website runs that **same, unmodified engine** in the visitor's
browser with [Pyodide](https://pyodide.org) inside a Web Worker and draws with plotly.js. There is no server, so it
deploys to Netlify as a static site.

## How it fits together

```
 GitHub ──push──▶ Netlify: npm ci && npm run build  (Node only)        Visitor's browser
                     │                                                ┌─────────────────────────────────┐
                     ├─ index.html + JS bundle (plotly.js, jsPDF) ───▶│ UI: 4 pages, Beginner/Expert    │
                     ├─ engine/engine.json  (the .py files, as-is) ──▶│     │ postMessage (JSON only)   │
                     ├─ topojson/world_110m.json (self-hosted map)  ─▶│     ▼                           │
                     └─ sw.js (offline cache)                         │ Web Worker: Pyodide 314.0.8     │
            Pyodide + NumPy + SciPy come from the jsDelivr CDN ──────▶│   oi_bridge.api.run_mission()   │
            (pinned version; ~25 MB, downloaded once, then cached)    │   → orbital_intelligence (engine)│
                                                                      └─────────────────────────────────┘
```

| Folder | What it is |
|---|---|
| `orbital_intelligence/` | Physics engine (Kepler, RK4/DOP853, J2, frames, launch, ground track, coverage, rules, reports). No UI code. |
| `oi_bridge/` | JSON boundary: `run_mission(config_json) -> result_json`, plus `analyse()` and `figure()` that reuse the last run. Figures are plain Plotly JSON built in Python. |
| `web/` | Vite site: `index.html`, `src/` (UI), `public/pyodide-worker.js`, `public/sw.js`, Playwright tests. |
| `tests/` | pytest: engine, bridge, review-defect regressions. |

Units inside the engine are km, km/s, s, rad, kg (thrust in kN). Degrees appear only at the UI edge.

## Run it locally

```bash
# Python engine + tests
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest
```

```bash
# Website (needs Node 20+)
cd web
npm install
npm run dev            # http://localhost:5173   (no offline cache in dev mode)
npm run build && npm run preview     # production build at http://localhost:4173
npx playwright install chromium && npx playwright test     # browser tests
```

## Deploy to Netlify from GitHub

1. Push this repository to GitHub.
2. In Netlify: **Add new site → Import an existing project → GitHub**, pick the repo.
3. Netlify reads `netlify.toml` (base `web`, build `npm ci && npm run build`, publish `dist`). Nothing to type in.
4. Every push builds; pull requests get preview URLs. `.github/workflows/ci.yml` runs pytest and the browser tests on every push.

Hosting notes: Pyodide/NumPy/SciPy load from the jsDelivr CDN, so Netlify only serves the ~1.7 MB (gzipped) site.
Newer Netlify free accounts use a credit model where each *production* deploy costs credits; prefer branch/preview
deploys and merge to the production branch sparingly. Check your plan under *Usage & billing*.

## The four pages

1. **Mission Setup**: orbit (presets, perigee/apogee or a/e, true or mean anomaly), propagation, ground targets,
   sensor, coverage region, optional launch (site, target, azimuth branch, vehicle). Load/save mission files, copy a share link.
2. **Launch & Orbit**: ascent telemetry and ascent ground track, flight events, analytical Δv budget, launch verdicts;
   orbit metrics; 3-D orbit (ECI/ECEF) and ground-track map with a time slider; pass tables.
3. **Analyse & Compare**: J2 vs two-body (separation, RAAN/inclination, measured vs analytical drift), coverage and
   revisit, all rule verdicts, numerical validation, parameter sweep and osculating elements (Expert).
4. **Export**: mission + results JSON (reloadable), ephemeris CSV, PDF and Markdown report.

Every metric card and table column has **Explain**: what it means, the formula, the engine function it comes from,
and its limits. **Beginner/Expert** hides or shows advanced inputs and plots. The demo mission runs automatically on first load.

## What works (measured, not claimed)

| Item | Evidence |
|---|---|
| Engine runs unchanged in the browser | Same results as native Python for the demo (coverage 0.5663, launch inclination 97.80°, azimuth 199.79°) |
| First load / run times (headless Chromium, this laptop) | Engine ready ≈ 16–23 s on first visit (downloads Pyodide+NumPy+SciPy); demo mission 0.5 s; launch auto-tune ≈ 17 s; validation 1.3 s |
| Offline after first visit | Playwright test: network off, page reload, engine starts from cache, demo runs, map draws |
| No console errors | Playwright fails on any console error, page error or failed request |
| Figures are valid | pytest builds every figure with the real `plotly` validator; the browser test runs `Plotly.validate` on the exact engine JSON |
| Numerical core | Kepler vs DOP853 < 1e-4 km; RK4 order ≈ 4; energy/h drift ~1e-10; J2 node drift vs closed form within 2 % (checks in `tests/`) |
| Reproducibility | Exported JSON contains the full config + engine version; reloading it reproduces identical summary, passes and coverage (tested) |

### Test output (18 Nov 2026)

```
$ python -m pytest -q
87 passed in 31.92s

$ npx playwright test
  ✓  demo mission runs end to end with no console errors (26.3s)
  ✓  launch to orbit: Sriharikota SSO flies south and is range-flagged (18.1s)
  ✓  bad input shows a clear error instead of crashing (0.3s)
  ✓  works offline after the first visit (Wi-Fi off) (8.1s)
  4 passed (54.3s)
```

Baseline before this work (original code, real packages): `64 passed, 1 skipped`. The skipped test was the only
one that used real Streamlit; the other UI tests used stubs that accepted any Plotly property.

## Fixed defects (from the third-eye review)

| ID | Problem | Fix | Proof |
|---|---|---|---|
| D1 | Ground Track page crashed: `layout.geo.gridcolor` is not a Plotly property | Grid colour set on `lataxis`/`lonaxis` | Real-plotly test failed on the old code with `Invalid property … Geo: 'gridcolor'`, passes after; negative-control test |
| D2 | Coverage silently dropped time samples on fine grids | Every time sample used, work chunked by grid cells; clear error if a job is too large | Demo region at 0.5°: was **37.6 %**, now **55.0 %**, identical to an un-chunked brute-force reference |
| D3 | Launch azimuth: only one of two branches; Sriharikota SSO flew north over India | Both branches; `auto` picks the one nearest an approximate site corridor; warning when outside | Default ascent now heads south (lat 13.7° → 3.1° N) and is flagged as needing a dog-leg |
| D4 | Peak pass elevation limited by sampling (true 90° pass shown as 78.5°) | Parabola fit to range² between samples | Synthetic overhead pass: > 89.5° at both 30 s and 120 s steps |
| D5 | JSON export could not reproduce a run | Export includes full config + versions; **Load mission file** | Round-trip test |
| D6 | UI tests could not detect library errors | Stubs removed; real plotly in pytest; real plotly.js + Pyodide in Playwright; CI | — |
| D8 | Map needed internet | World map self-hosted and cached by the service worker | Offline test |
| D11 | Mean anomaly not exposed | Anomaly type selector (Expert) | Test |

Found while building the site: the old figures used `template="plotly_dark"`, a plotly-**Python** shortcut that
plotly.js does not understand; removed. Also, `Plotly.validate` in plotly.js 4.3.0 throws on any `layout.legend`,
so the browser test validates legend keys against plotly.js's schema separately.

## What is approximate (read before trusting numbers)

* **Gravity**: two-body + J2 only. No drag, solar pressure, Sun/Moon or higher harmonics in orbit propagation.
* **Earth rotation**: GMST rotation only (no precession, nutation, polar motion). Fine for design-level work.
* **Coverage**: spherical Earth, nadir-pointing sensor, evaluated at output steps.
* **AOS/LOS**: linear interpolation between samples (≈1–2 s error at 60 s steps).
* **Lifetime**: one exponential atmosphere at mean solar activity, circular orbit. Order of magnitude only.
* **Launch vehicle**: illustrative Falcon-9-class numbers, not an official model. Point-mass 3-DOF ascent;
  hand-drawn Cd(Mach); no angle of attack, q·α, throttle, or fairing jettison. Max-Q and g-load are *outputs*, not
  constraints (the default run peaks at 6.6 g and the app warns). Guidance is feasible, not optimal, so losses
  (2–3 km/s) are higher than flown vehicles.
* **Range-safety corridors** (`launch.RANGE_CORRIDORS`) are approximate planning values from general references,
  **not official range data**. No dog-leg manoeuvre is simulated, so a Sriharikota polar launch is (correctly) flagged.
* **Analytical Δv budget** assumes 1 km/s of losses; the ascent simulation reports actual losses.

## What is unfinished

* **Not validated against any flown launch profile** (review D12). The launch model has only internal checks.
* **No external orbit reference** (SGP4/GMAT) and no TLE import yet; the "two-body is 10 km off after N minutes"
  figure on page 3 is from this engine only.
* **Headline experiment** (prediction-horizon heat-map over altitude × inclination) not built.
* No throttle schedule / max-Q constraint (D7), no α or fairing (D13), plain-sphere 3-D Earth with no texture (D14).
* PDF is generated with jsPDF from the engine's report tables; it has no plots.
* First visit needs ~25 MB from the CDN; very slow connections will wait. Only Chromium is covered by the automated browser tests.
* No `LICENSE` or `CITATION.cff` yet: choosing a licence is the team's decision.
* `npm audit` reports a ReDoS advisory in a dependency of the Vite build tool (dev-time only; not shipped to visitors).

## AI assistance

Large parts of this code were generated with AI assistance and then checked by running the tests above. Add your
institute's required AI-assistance statement here, including what each team member verified and changed.

See `docs/TECHNICAL_REFERENCE.md` for equations and validation evidence.
