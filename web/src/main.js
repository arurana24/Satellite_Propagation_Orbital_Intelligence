import Plotly from "plotly.js-dist-min";
import { Engine } from "./engine.js";
import { EXPLAIN } from "./explain.js";

window.Plotly = Plotly;
window.__oiFigures = {};                                  // exposed for the browser test (Plotly.validate) and debugging
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const num = (x, nd = 2) => (x === null || x === undefined || Number.isNaN(x) ? "–" : Number(x).toFixed(nd));
const PLOT_CONFIG = { responsive: true, displaylogo: false, topojsonURL: new URL("topojson/", location.href).href,
  modeBarButtonsToRemove: ["select2d", "lasso2d"] };

const PRESETS = {
  "Sun-synchronous 600 km": { hp: 600, ha: 600, i: 97.79, raan: 0, argp: 0, anom: 0 },
  "LEO – ISS-like": { hp: 410, ha: 420, i: 51.64, raan: 0, argp: 0, anom: 0 },
  "LEO – equatorial 500 km": { hp: 500, ha: 500, i: 0, raan: 0, argp: 0, anom: 0 },
  "MEO – GPS-like": { hp: 20180, ha: 20180, i: 55, raan: 0, argp: 0, anom: 0 },
  "GEO": { hp: 35786, ha: 35786, i: 0, raan: 0, argp: 0, anom: 0 },
  "HEO – Molniya": { hp: 600, ha: 39700, i: 63.4, raan: 0, argp: 270, anom: 0 },
};

const S = { meta: null, config: null, result: null, validation: null, busy: false, engineReady: false };
let engine;

// ============================================================================ helpers
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

function metric(id, label, value, cls = "") {
  return `<button type="button" class="metric ${cls}" data-explain="${id}" data-value="${esc(value)}" data-label="${esc(label)}">
    <span class="m-label"><span>${esc(label)}</span><span class="m-why">Explain ⓘ</span></span><span class="m-value">${esc(value)}</span></button>`;
}
const th = (label, id) => `<th>${esc(label)}${id ? ` <button type="button" class="th-why" data-explain="${id}" data-label="${esc(label)}" aria-label="Explain ${esc(label)}">ⓘ</button>` : ""}</th>`;

function findingsHTML(findings) {
  const order = { error: 0, warning: 1, info: 2, ok: 3 };
  return [...findings].sort((a, b) => order[a.severity] - order[b.severity]).map((f) => `
    <div class="finding ${f.severity}"><span class="tag ${f.severity}">${f.severity.toUpperCase()}</span><b>${esc(f.title)}</b>
      <div class="sub">${esc(f.message)}</div>
      ${f.suggestion ? `<div class="fix">→ ${esc(f.suggestion)}</div>` : ""}
      ${f.value || f.basis ? `<div class="sub">${f.value ? `Measured: ${esc(f.value)}. ` : ""}${f.basis ? `Basis: ${esc(f.basis)}. ` : ""}<span class="muted">Rule <code>${esc(f.rule)}</code></span></div>` : ""}
    </div>`).join("");
}

function plot(id, fig) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!fig) { Plotly.purge(el); el.innerHTML = ""; delete window.__oiFigures[id]; return; }
  window.__oiFigures[id] = JSON.stringify(fig);         // exact engine output, kept for Plotly.validate in tests
  Plotly.react(el, fig.data, fig.layout, PLOT_CONFIG);
}

function download(name, content, type) {
  const blob = content instanceof Blob ? content : new Blob([content], { type });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
const safeName = (s) => String(s || "mission").replace(/[^\w.-]+/g, "_").slice(0, 60);

// ============================================================================ explain panel
function openExplain(id, label, value) {
  const e = EXPLAIN[id];
  const body = $("#explain-body");
  if (!e) { body.innerHTML = `<p>No explanation recorded for <code>${esc(id)}</code>.</p>`; }
  else {
    body.innerHTML = `<h3 style="margin-top:6px">${esc(e.title)}</h3>
      ${value ? `<div class="x-value">${esc(value)}</div>` : ""}
      <dl>
        <dt>What it means</dt><dd>${esc(e.plain)}</dd>
        <dt>How it is computed</dt><dd>${esc(e.how)}</dd>
        <dt>Engine source</dt><dd><code>${esc(e.source)}</code></dd>
        ${e.caveat ? `<dt>Limits</dt><dd>${esc(e.caveat)}</dd>` : ""}
        <dt>Where the number comes from</dt><dd>${S.result ? `This mission run (engine v${esc(S.result.engine_version)}), computed in your browser. Nothing on screen is typed in by hand.` : "A mission run."}</dd>
      </dl>`;
  }
  const panel = $("#explain");
  panel.classList.add("open"); panel.setAttribute("aria-hidden", "false");
  $("#explain-close").focus();
}
document.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-explain]");
  if (t) openExplain(t.dataset.explain, t.dataset.label, t.dataset.value);
});
$("#explain-close").addEventListener("click", () => { $("#explain").classList.remove("open"); $("#explain").setAttribute("aria-hidden", "true"); });
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") $("#explain-close").click(); });

// ============================================================================ routing + mode
const PAGES = ["setup", "orbit", "analyse", "export"];
function route() {
  const page = PAGES.includes(location.hash.slice(1)) ? location.hash.slice(1) : "setup";
  for (const s of $$(".page")) s.hidden = s.dataset.page !== page;
  for (const a of $$(".tabs a")) { a.classList.toggle("active", a.dataset.page === page); a.setAttribute("aria-selected", a.dataset.page === page); }
  requestAnimationFrame(() => $$(`#page-${page} .js-plotly-plot`).forEach((el) => Plotly.Plots.resize(el)));
}
window.addEventListener("hashchange", route);

function setMode(mode) {
  document.body.classList.toggle("mode-expert", mode === "expert");
  document.body.classList.toggle("mode-beginner", mode !== "expert");
  for (const b of $$(".mode-toggle button")) b.setAttribute("aria-pressed", b.dataset.mode === mode);
  store.set("oi-mode", mode);
}
for (const b of $$(".mode-toggle button")) b.addEventListener("click", () => setMode(b.dataset.mode));

// ============================================================================ form <-> config
const F = (id) => document.getElementById(id);
const val = (id) => F(id).value;
const fnum = (id) => { const v = parseFloat(F(id).value); return Number.isFinite(v) ? v : null; };

function renderTargets(targets) {
  $("#targets-table tbody").innerHTML = targets.map((t) => `<tr>
    <td><input data-k="name" value="${esc(t.name)}" maxlength="40" aria-label="Target name" /></td>
    <td><input data-k="lat_deg" type="number" step="0.01" min="-90" max="90" value="${t.lat_deg}" aria-label="Latitude" /></td>
    <td><input data-k="lon_deg" type="number" step="0.01" min="-180" max="180" value="${t.lon_deg}" aria-label="Longitude" /></td>
    <td class="expert"><input data-k="alt_km" type="number" step="0.01" value="${t.alt_km ?? 0}" aria-label="Altitude" /></td>
    <td class="expert"><input data-k="min_elev_deg" type="number" step="1" min="0" max="89" value="${t.min_elev_deg ?? 10}" aria-label="Minimum elevation" /></td>
    <td><button type="button" class="small" data-remove aria-label="Remove target">✕</button></td></tr>`).join("");
}
function readTargets() {
  return $$("#targets-table tbody tr").map((tr) => {
    const o = {}; for (const i of $$("input", tr)) o[i.dataset.k] = i.type === "number" ? parseFloat(i.value) : i.value.trim();
    return o;
  }).filter((t) => t.name);
}
$("#targets-table").addEventListener("click", (ev) => { if (ev.target.closest("[data-remove]")) ev.target.closest("tr").remove(); });
$("#btn-add-target").addEventListener("click", () => renderTargets([...readTargets(), { name: `Target ${readTargets().length + 1}`, lat_deg: 0, lon_deg: 0, alt_km: 0, min_elev_deg: 10 }]));

function renderStages(stages) {
  $("#stages-table tbody").innerHTML = stages.map((s) => `<tr>
    <td><input data-k="name" value="${esc(s.name)}" aria-label="Stage name" /></td>
    ${["dry_mass_kg", "prop_mass_kg", "thrust_sl_kn", "thrust_vac_kn", "isp_vac_s"].map((k) => `<td><input data-k="${k}" type="number" min="0" value="${s[k]}" aria-label="${k}" /></td>`).join("")}</tr>`).join("");
}
function readStages() {
  return $$("#stages-table tbody tr").map((tr) => {
    const o = {}; for (const i of $$("input", tr)) o[i.dataset.k] = i.type === "number" ? parseFloat(i.value) : i.value; return o;
  });
}

function toLocalInput(iso) { return String(iso).slice(0, 19); }

function writeForm(c) {
  F("f-name").value = c.name; F("f-epoch").value = toLocalInput(c.epoch);
  const o = c.orbit;
  F("f-mode").value = o.mode; F("f-hp").value = o.perigee_alt_km; F("f-ha").value = o.apogee_alt_km; F("f-a").value = o.a_km; F("f-e").value = o.e;
  F("f-i").value = o.i_deg; F("f-raan").value = o.raan_deg; F("f-argp").value = o.argp_deg; F("f-anom").value = o.anomaly_deg; F("f-anomtype").value = o.anomaly_type;
  const p = c.propagation;
  F("f-dur").value = p.duration_h; F("f-dt").value = p.dt_s; F("f-model").value = p.model; F("f-method").value = p.method;
  renderTargets(c.targets);
  F("f-fov").value = c.sensor.half_fov_deg ?? 0; F("f-a2m").value = c.area_to_mass;
  const r = c.region;
  F("f-reg-on").checked = !!r.enabled; F("f-la0").value = r.lat_min; F("f-la1").value = r.lat_max; F("f-lo0").value = r.lon_min; F("f-lo1").value = r.lon_max;
  F("f-grid").value = String(r.grid_deg);
  F("f-site-check").value = c.launch_site ?? "None";
  const l = c.launch;
  F("f-launch-on").checked = !!l.enabled; F("f-l-site").value = l.site; F("f-l-alt").value = l.target_alt_km; F("f-l-inc").value = l.target_incl_deg;
  F("f-l-branch").value = l.branch; F("f-l-guid").value = l.guidance;
  F("f-l-az").value = l.manual.azimuth_deg ?? ""; F("f-l-vert").value = l.manual.vertical_time_s; F("f-l-kick").value = l.manual.kick_angle_deg; F("f-l-pitch").value = l.manual.upper_pitch_end_deg;
  renderStages(l.vehicle.stages);
  F("f-v-pl").value = l.vehicle.payload_kg; F("f-v-area").value = l.vehicle.ref_area_m2; F("f-v-cd").value = l.vehicle.cd_scale;
  syncConditional();
}

function readForm() {
  const base = structuredClone(S.config || S.meta.default_config);
  const c = {
    ...base, name: val("f-name") || "Mission", epoch: val("f-epoch") || base.epoch,
    orbit: { mode: val("f-mode"), perigee_alt_km: fnum("f-hp"), apogee_alt_km: fnum("f-ha"), a_km: fnum("f-a"), e: fnum("f-e"), i_deg: fnum("f-i"),
      raan_deg: fnum("f-raan") ?? 0, argp_deg: fnum("f-argp") ?? 0, anomaly_deg: fnum("f-anom") ?? 0, anomaly_type: val("f-anomtype") },
    propagation: { duration_h: fnum("f-dur"), dt_s: fnum("f-dt"), model: val("f-model"), method: val("f-method") },
    targets: readTargets(), sensor: { half_fov_deg: fnum("f-fov") ?? 0 }, area_to_mass: fnum("f-a2m"),
    region: { enabled: F("f-reg-on").checked, lat_min: fnum("f-la0"), lat_max: fnum("f-la1"), lon_min: fnum("f-lo0"), lon_max: fnum("f-lo1"), grid_deg: parseFloat(val("f-grid")) },
    launch_site: val("f-site-check") === "None" ? null : val("f-site-check"),
    launch: { ...base.launch, enabled: F("f-launch-on").checked, site: val("f-l-site"), target_alt_km: fnum("f-l-alt"), target_incl_deg: fnum("f-l-inc"),
      branch: val("f-l-branch"), guidance: val("f-l-guid"),
      manual: { azimuth_deg: fnum("f-l-az"), vertical_time_s: fnum("f-l-vert"), kick_angle_deg: fnum("f-l-kick"), upper_pitch_end_deg: fnum("f-l-pitch") },
      vehicle: { ...base.launch.vehicle, stages: readStages(), payload_kg: fnum("f-v-pl"), ref_area_m2: fnum("f-v-area"), cd_scale: fnum("f-v-cd") } },
  };
  for (const [k, v] of Object.entries(c.orbit)) if (v === null && k !== "anomaly_type") throw new Error(`Orbit: "${k}" needs a number.`);
  for (const [k, v] of Object.entries(c.propagation)) if (v === null) throw new Error(`Propagation: "${k}" needs a number.`);
  return c;
}

function syncConditional() {
  for (const el of $$("[data-mode-show]")) el.hidden = el.dataset.modeShow !== val("f-mode");
  for (const el of $$("[data-guid-show]")) el.hidden = el.dataset.guidShow !== val("f-l-guid");
}
F("f-mode").addEventListener("change", syncConditional);
F("f-l-guid").addEventListener("change", syncConditional);
F("f-preset").addEventListener("change", () => {
  const p = PRESETS[val("f-preset")]; if (!p) return;
  F("f-mode").value = "altitudes"; F("f-hp").value = p.hp; F("f-ha").value = p.ha; F("f-i").value = p.i;
  F("f-raan").value = p.raan; F("f-argp").value = p.argp; F("f-anom").value = p.anom; F("f-anomtype").value = "true";
  syncConditional();
});

// ============================================================================ share links / files
const b64e = (s) => btoa(unescape(encodeURIComponent(s))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const b64d = (s) => decodeURIComponent(escape(atob(s.replace(/-/g, "+").replace(/_/g, "/"))));

$("#btn-share").addEventListener("click", async () => {
  try {
    const c = readForm();
    const url = `${location.origin}${location.pathname}?m=${b64e(JSON.stringify(c))}#setup`;
    try { await navigator.clipboard.writeText(url); $("#run-status").textContent = "Share link copied to clipboard."; }
    catch { prompt("Copy this link:", url); }
  } catch (e) { showSetupError(e.message); }
});

F("file-load").addEventListener("change", async (ev) => {
  const f = ev.target.files[0]; if (!f) return;
  try {
    const data = JSON.parse(await f.text());
    const cfg = data.config || data;                    // accept an exported result or a bare config
    if (!cfg.orbit) throw new Error("This file has no mission configuration (expected a JSON exported by Orbital Intelligence).");
    S.config = cfg; writeForm(mergeDefaults(cfg)); $("#run-status").textContent = `Loaded ${f.name}. Press Run mission.`;
  } catch (e) { showSetupError(`Could not load file: ${e.message}`); }
  ev.target.value = "";
});

function mergeDefaults(cfg) {
  const merge = (a, b) => { const o = { ...a }; for (const [k, v] of Object.entries(b || {})) o[k] = v && typeof v === "object" && !Array.isArray(v) && a?.[k] && typeof a[k] === "object" ? merge(a[k], v) : v; return o; };
  return merge(S.meta.default_config, cfg);
}

function showSetupError(msg) { const e = $("#setup-error"); e.textContent = msg; e.hidden = !msg; }

// ============================================================================ running
function setBusy(b, label = "") {
  S.busy = b;
  for (const id of ["btn-run", "btn-demo", "btn-compare", "btn-validate", "btn-sweep"]) F(id).disabled = b || !S.engineReady;
  $("#run-status").textContent = label;
}

async function runMission(cfg) {
  showSetupError("");
  const launching = cfg.launch?.enabled;
  const t0 = performance.now();
  const timer = setInterval(() => setBusy(true, `${launching ? "Simulating ascent and propagating" : "Propagating"}… ${((performance.now() - t0) / 1000).toFixed(0)} s`), 500);
  setBusy(true, launching ? "Simulating ascent (auto-tuning can take 10–30 s)…" : "Propagating…");
  try {
    const res = await engine.run(cfg);
    S.config = cfg; S.result = res; S.validation = null;
    renderResult();
    setBusy(false, `Done in ${res.compute_seconds.toFixed(1)} s. See pages 2–4.`);
  } catch (e) {
    setBusy(false, "");
    showSetupError(`${e.kind === "input" ? "Input problem" : "Engine error"}: ${e.message}`);
  } finally { clearInterval(timer); }
}

$("#btn-run").addEventListener("click", () => { try { runMission(readForm()); } catch (e) { showSetupError(e.message); } });
$("#btn-demo").addEventListener("click", () => { const c = structuredClone(S.meta.default_config); writeForm(c); F("f-preset").value = "Sun-synchronous 600 km"; runMission(c); });

// ============================================================================ rendering results
function orbitMetrics(r) {
  const s = r.summary, inv = r.invariants;
  const st = r.status === "infeasible" ? "bad" : r.status === "warning" ? "warn" : "good";
  return [
    metric("orbit_alt", "Altitude (perigee × apogee)", `${num(s.perigee_alt_km, 0)} × ${num(s.apogee_alt_km, 0)} km`),
    metric("incl", "Inclination", `${num(s.i_deg, 2)}°`),
    metric("period", "Period", `${num(s.period_min, 2)} min`),
    metric("status", "Feasibility", r.status.toUpperCase(), st),
    metric("v_perigee", "Speed at perigee", `${num(s.v_perigee_kms, 3)} km/s`),
    metric("raan_rate", "RAAN drift (J2)", `${s.raan_rate_deg_day >= 0 ? "+" : ""}${num(s.raan_rate_deg_day, 3)} °/day`),
    metric("energy_drift", "Energy drift (this run)", Number(inv.energy_rel_drift).toExponential(1)),
    metric("source", "Initial state", r.initial_state_source === "launch" ? "Launch hand-off" : "Orbital elements"),
  ];
}

function renderResult() {
  const r = S.result;
  document.body.classList.add("has-result");
  // ---------------- setup page summary
  $("#setup-results").hidden = false;
  $("#setup-metrics").innerHTML = orbitMetrics(r).join("");
  $("#setup-findings").innerHTML = findingsHTML(r.findings);
  // ---------------- orbit page
  const s = r.summary;
  $("#orbit-metrics").innerHTML = [...orbitMetrics(r),
    metric("sma", "Semi-major axis", `${num(s.a_km, 1)} km`), metric("ecc", "Eccentricity", num(s.e, 5)),
    metric("mean_motion", "Revolutions per day", num(s.mean_motion_rev_day, 3)), metric("argp_rate", "Perigee drift (J2)", `${num(s.argp_rate_deg_day, 3)} °/day`),
    metric("sso_incl", "Sun-sync inclination here", s.sso_inclination_deg ? `${num(s.sso_inclination_deg, 2)}°` : "n/a"),
    metric("energy", "Specific energy", `${num(s.energy_km2s2, 3)} km²/s²`),
    metric("angmom_drift", `${r.invariants.invariant_used} drift (this run)`, Number(r.invariants.angmom_rel_drift).toExponential(1)),
    metric("nfev", "Function evaluations", String(r.nfev))].join("");
  renderLaunch(r.launch);
  const slider = F("v-time");
  slider.max = String(r.n_samples - 1); slider.step = String(r.track.stride); slider.value = "0";
  updateNow(0);
  plot("plot-3d", r.figures.earth_3d); plot("plot-gt", r.figures.ground_track);
  $("#pass-tables").innerHTML = Object.keys(r.passes).length ? Object.entries(r.passes).map(([name, ps]) => `<h3>${esc(name)}</h3>${ps.length ? `
    <div class="table-wrap"><table class="data"><thead><tr>${th("AOS (UTC)", "aos")}${th("LOS (UTC)", "los")}${th("Duration [s]", "duration")}${th("Peak elev [deg]", "max_elev")}${th("AOS az [deg]", "az")}${th("LOS az [deg]", "az")}${th("Clipped", "clipped")}</tr></thead>
    <tbody>${ps.map((p) => `<tr><td>${p.aos_utc}</td><td>${p.los_utc.slice(11)}</td><td class="num">${num(p.duration_s, 0)}</td><td class="num">${num(p.max_elev_deg, 1)}</td><td class="num">${num(p.aos_az_deg, 0)}</td><td class="num">${num(p.los_az_deg, 0)}</td><td>${p.clipped ? "yes" : ""}</td></tr>`).join("")}</tbody></table></div>`
    : `<p class="muted">No passes above the minimum elevation in this window.</p>`}`).join("") : `<p class="muted">No ground targets configured.</p>`;
  // ---------------- analyse page
  const cov = r.coverage;
  $("#coverage-metrics").innerHTML = cov ? [metric("cov_fraction", "Region area covered", `${num(100 * cov.fraction, 1)} %`),
    metric("cov_revisit", "Mean worst-case revisit gap", cov.mean_revisit_s === null ? "–" : `${num(cov.mean_revisit_s / 3600, 2)} h`),
    metric("cov_cells", "Grid cells", `${cov.cells} @ ${cov.grid_deg}°`), metric("cov_samples", "Time samples used", String(cov.time_samples))].join("")
    : `<p class="muted">No coverage region (enable one and set a sensor half-angle in Mission Setup).</p>`;
  const rv = Object.entries(r.revisit);
  $("#revisit-table").innerHTML = rv.length ? `<thead><tr>${th("Target")}${th("Passes", "n_passes")}${th("Total access [min]", "total_access")}${th("Mean gap [h]", "mean_gap")}${th("Max gap [h]", "max_gap")}${th("First access [h]", "first_access")}${th("Best elev [deg]", "best_elev")}</tr></thead>
    <tbody>${rv.map(([n, v]) => `<tr><td>${esc(n)}</td><td class="num">${v.n_passes}</td><td class="num">${num(v.total_access_s / 60, 1)}</td><td class="num">${v.mean_gap_s === null ? "–" : num(v.mean_gap_s / 3600, 2)}</td><td class="num">${v.max_gap_s === null ? "–" : num(v.max_gap_s / 3600, 2)}</td><td class="num">${v.first_access_s === null ? "–" : num(v.first_access_s / 3600, 2)}</td><td class="num">${v.n_passes ? num(v.max_elev_deg, 1) : "–"}</td></tr>`).join("")}</tbody>` : "";
  plot("plot-timeline", rv.length ? r.figures.pass_timeline : null);
  plot("plot-heat", r.figures.coverage_heatmap); plot("plot-cumcov", r.figures.coverage_time);
  $("#analyse-findings").innerHTML = findingsHTML([...r.findings, ...(r.launch?.findings || [])]);
  plot("plot-osc", r.figures.osculating);
  for (const id of ["compare-metrics", "validation-table", "sweep-table"]) F(id).innerHTML = "";
  for (const id of ["plot-sep", "plot-raan", "plot-incl", "plot-conv", "plot-sweep"]) plot(id, null);
  $("#compare-status").textContent = ""; $("#validate-status").textContent = "";
  // ---------------- export page
  renderExport();
  route();
}

function renderLaunch(L) {
  $("#launch-block").hidden = !L;
  if (!L) return;
  const s = L.summary;
  const okCls = s.reached_target_energy ? "ok" : "error";
  const other = L.branches_relative_deg ? Object.entries(L.branches_relative_deg).filter(([b]) => b !== L.branch).map(([b, a]) => `${b}-going ${num(a, 1)}°`).join("") : "";
  $("#launch-status").innerHTML = `<div class="alert ${okCls}">${esc(L.status)}${L.converged === false ? " — auto-tuner did not reach the target within tolerance; showing the best trajectory found." : ""}</div>
    <p class="hint">Azimuth branch: <b>${esc(L.branch)}-going</b> (${esc(L.branch_reason)}).${other ? ` Other branch: ${esc(other)}.` : ""}
    ${L.corridor_deg ? ` Approximate corridor for ${esc(L.site.name)}: ${L.corridor_deg[0]}–${L.corridor_deg[1]}° (planning value, not official).` : " No corridor data for this site."}</p>`;
  $("#launch-metrics").innerHTML = [
    metric("burnout_time", "Time to orbit", `${num(s.burnout_time_s, 0)} s`), metric("burnout_alt", "Burnout altitude", `${num(s.burnout_alt_km, 0)} km`),
    metric("orbit_achieved", "Orbit achieved", s.perigee_alt_km !== undefined ? `${num(s.perigee_alt_km, 0)} × ${num(s.apogee_alt_km, 0)} km` : "not bound", s.perigee_alt_km > 160 ? "good" : "bad"),
    metric("launch_incl", "Inclination", s.inclination_deg !== undefined ? `${num(s.inclination_deg, 2)}°` : "–"),
    metric("azimuth", "Launch azimuth", `${num(s.launch_azimuth_deg, 1)}° (${s.heading})`, s.in_corridor === false ? "warn" : s.in_corridor ? "good" : ""),
    metric("max_q", "Max-Q", `${num(s.max_q_kpa, 1)} kPa @ ${num(s.max_q_alt_km, 1)} km`),
    metric("max_accel", "Peak acceleration", `${num(s.max_accel_g, 2)} g`, s.max_accel_g > 6 ? "warn" : ""),
    metric("losses", "Total losses", `${num(s.total_losses_kms, 2)} km/s`), metric("downrange", "Downrange", `${num(s.downrange_km, 0)} km`),
    metric("propellant", "Propellant used", `${num(s.propellant_used_kg / 1000, 1)} t`), metric("engine_dv", "Engine Δv", `${num(s.engine_dv_kms, 2)} km/s`),
    metric("burnout_speed", "Burnout inertial speed", `${num(s.burnout_speed_inertial_kms, 3)} km/s`)].join("");
  const a = L.analytical;
  $("#launch-budget").innerHTML = a.feasible ? [
    metric("v_circ", "Circular speed", `${num(a.v_circ_kms, 3)} km/s`), metric("rotation_benefit", "Rotation benefit", `${num(a.rotation_benefit_kms * 1000, 0)} m/s`),
    metric("dv_required", "Required Δv (est.)", `${num(a.dv_required_kms, 2)} km/s`), metric("vehicle_dv", "Vehicle Δv (ideal)", `${num(a.vehicle_dv_kms, 2)} km/s`),
    metric("margin", "Margin", `${num(a.margin_kms, 2)} km/s`, a.margin_kms < 0 ? "bad" : ""), metric("max_payload", "Max payload (est.)", `${num(a.max_payload_kg, 0)} kg`),
    metric("liftoff_mass", "Liftoff mass", `${num(L.liftoff_mass_kg / 1000, 1)} t`)].join("") : `<div class="alert error">${esc(a.message)}</div>`;
  $("#launch-events").innerHTML = `<thead><tr>${th("t [s]")}${th("Event")}</tr></thead><tbody>${L.events.map(([t, n]) => `<tr><td class="num">${num(t, 1)}</td><td>${esc(n)}</td></tr>`).join("")}</tbody>`;
  $("#launch-findings").innerHTML = findingsHTML(L.findings);
  plot("plot-ascent", L.figures.ascent); plot("plot-ascent-track", L.figures.ascent_track);
}

function updateNow(idx) {
  const r = S.result; const k = Math.min(r.track.t.length - 1, Math.round(idx / r.track.stride));
  const t = r.track.t[k];
  F("v-time-label").textContent = `${num(t / 60, 1)} min (${num(t / 3600, 2)} h)`;
  $("#now-metrics").innerHTML = [metric("now_alt", "Altitude now", `${num(r.track.alt[k], 1)} km`), metric("now_speed", "Inertial speed now", `${num(r.track.speed[k], 4)} km/s`),
    metric("now_sub", "Sub-satellite point", `${num(r.track.lat[k], 2)}°, ${num(r.track.lon[k], 2)}°`)].join("");
}

let redrawTimer = null; let redrawSeq = 0;
function scheduleRedraw() {
  clearTimeout(redrawTimer);
  redrawTimer = setTimeout(async () => {
    const seq = ++redrawSeq; const idx = parseInt(F("v-time").value, 10);
    try {
      const [a, b] = await Promise.all([engine.figure({ name: "earth_3d", idx, frame: val("v-frame") }),
        engine.figure({ name: "ground_track", idx, footprint: F("v-foot").checked })]);
      if (seq !== redrawSeq) return;
      plot("plot-3d", a.figure); plot("plot-gt", b.figure);
    } catch (e) { console.warn("redraw failed", e); }
  }, 120);
}
F("v-time").addEventListener("input", () => { updateNow(parseInt(F("v-time").value, 10)); scheduleRedraw(); });
F("v-frame").addEventListener("change", scheduleRedraw);
F("v-foot").addEventListener("change", scheduleRedraw);

// ============================================================================ analysis actions
async function analysis(btn, statusEl, req, onOk) {
  const st = F(statusEl); st.textContent = "Running…"; setBusy(true, "");
  try { const r = await engine.analyse(req); onOk(r); st.textContent = `Done in ${r.compute_seconds.toFixed(1)} s.`; }
  catch (e) { st.textContent = `Error: ${e.message}`; }
  finally { setBusy(false, ""); }
}
$("#btn-compare").addEventListener("click", () => analysis("btn-compare", "compare-status",
  { action: "compare", hours: fnum("c-hours"), step_s: fnum("c-step") }, (r) => {
    $("#compare-metrics").innerHTML = [metric("final_sep", "Final separation", `${num(r.final_sep_km, 1)} km`), metric("max_sep", "Max separation", `${num(r.max_sep_km, 1)} km`),
      metric("t_exceed", "Two-body 10 km off after", r.t_exceed_10km_min === null ? `> ${num(r.hours, 1)} h` : `${num(r.t_exceed_10km_min, 0)} min`),
      r.raan_rate_measured_deg_day !== undefined ? metric("raan_measured", "RAAN drift measured / theory", `${num(r.raan_rate_measured_deg_day, 4)} / ${num(r.raan_rate_analytical_deg_day, 4)} °/day`) : ""].join("");
    plot("plot-sep", r.figures.separation); plot("plot-raan", r.figures.raan); plot("plot-incl", r.figures.incl);
  }));
$("#btn-validate").addEventListener("click", () => analysis("btn-validate", "validate-status", { action: "validate" }, (r) => {
  S.validation = r.rows;
  $("#validation-table").innerHTML = `<thead><tr>${th("Check", "validation_value")}${th("Value")}${th("Threshold")}${th("Unit")}${th("Result")}</tr></thead><tbody>${r.rows.map((x) =>
    `<tr><td>${esc(x.check)}</td><td class="num">${x.value.toExponential(3)}</td><td class="num">${x.threshold.toExponential(1)}</td><td>${esc(x.unit)}</td><td><span class="tag ${x.passed ? "ok" : "error"}">${x.passed ? "PASS" : "FAIL"}</span></td></tr>`).join("")}</tbody>`;
  plot("plot-conv", r.figures.convergence);
  renderExport();
}));
$("#btn-sweep").addEventListener("click", () => analysis("btn-sweep", "compare-status",
  { action: "sweep", parameter: val("s-par"), start: fnum("s-from"), stop: fnum("s-to"), n: fnum("s-n"), plot: val("s-y").split(",") }, (r) => {
    plot("plot-sweep", r.figures.sweep);
    const cols = Object.keys(r.rows[0] || {});
    $("#sweep-table").innerHTML = `<thead><tr>${cols.map((c) => th(c)).join("")}</tr></thead><tbody>${r.rows.map((row) => `<tr>${cols.map((c) => `<td class="num">${row[c] === null ? "–" : Number(row[c]).toPrecision(6)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  }));
F("s-par").addEventListener("change", () => { const d = { altitude_km: [300, 2000], inclination_deg: [0, 180], eccentricity: [0, 0.6] }[val("s-par")]; F("s-from").value = d[0]; F("s-to").value = d[1]; });

// ============================================================================ export
function renderExport() {
  const r = S.result; if (!r) return;
  const secs = [...r.exports.sections];
  if (S.validation) secs.push(["Numerical validation", [["Check", "Value", "Threshold", "Result"], ...S.validation.map((x) => [x.check, x.value.toExponential(3), x.threshold.toExponential(1), x.passed ? "PASS" : "FAIL"])]]);
  $("#report-preview").innerHTML = secs.map(([h, rows]) => `<h3>${esc(h)}</h3><div class="table-wrap"><table><thead><tr>${rows[0].map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${rows.slice(1).map((row) => `<tr>${row.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`).join("");
  $("#export-validation-note").textContent = S.validation ? "Numerical validation results are included in the PDF." : "Tip: run Numerical validation on page 3 to include it in the PDF report.";
}
$("#dl-json").addEventListener("click", () => download(`${safeName(S.result.name)}_mission.json`, S.result.exports.json, "application/json"));
$("#dl-csv").addEventListener("click", () => download(`${safeName(S.result.name)}_ephemeris.csv`, S.result.exports.csv, "text/csv"));
$("#dl-md").addEventListener("click", () => download(`${safeName(S.result.name)}_report.md`, S.result.exports.markdown, "text/markdown"));
$("#dl-pdf").addEventListener("click", async () => {
  const { buildPdf } = await import("./pdf.js");
  download(`${safeName(S.result.name)}_report.pdf`, buildPdf(S.result, S.validation), "application/pdf");
});

// ============================================================================ offline support
async function setupServiceWorker() {
  if (!import.meta.env.PROD || !("serviceWorker" in navigator)) return;
  const register = () => navigator.serviceWorker.register("/sw.js").catch((e) => console.warn("Service worker unavailable; offline mode disabled.", e));
  // Returning visitor: already controlled, so never block start-up on the update check (it can hang offline).
  if (navigator.serviceWorker.controller) { if (navigator.onLine) register(); return; }
  if (!navigator.onLine) return;
  // First visit: wait briefly for the worker to take control so the engine downloads get cached.
  const controlled = new Promise((r) => navigator.serviceWorker.addEventListener("controllerchange", r, { once: true }));
  register();
  await Promise.race([controlled, new Promise((r) => setTimeout(r, 4000))]);
}
function cacheLoadedResources() {
  const ctl = navigator.serviceWorker?.controller; if (!ctl) return;
  const urls = new Set([location.origin + "/", location.origin + "/index.html"]);
  for (const e of performance.getEntriesByType("resource")) if (e.name.startsWith(location.origin) || e.name.startsWith("https://cdn.jsdelivr.net/pyodide/")) urls.add(e.name.split("#")[0]);
  for (const u of ["/engine/engine.json", "/topojson/world_110m.json", "/pyodide-worker.js"]) urls.add(location.origin + u);
  ctl.postMessage({ type: "cache-urls", urls: [...urls].filter((u) => !u.includes("?m=")) });
}

// ============================================================================ boot
async function boot() {
  setMode(store.get("oi-mode") === "expert" ? "expert" : "beginner");
  route();
  await setupServiceWorker();
  const bar = $("#engine-bar");
  engine = new Engine((p) => { F("engine-progress").style.width = `${p.pct}%`; F("engine-label").textContent = p.label; });
  const t0 = performance.now();
  const tick = setInterval(() => { F("engine-detail").textContent = `${((performance.now() - t0) / 1000).toFixed(0)} s · first visit downloads ~25 MB once, then it is cached`; }, 500);
  try {
    S.meta = await engine.init();
  } catch (e) {
    clearInterval(tick);
    bar.classList.add("failed");
    F("engine-label").textContent = `The physics engine could not start: ${e.message}`;
    F("engine-detail").textContent = navigator.onLine ? "Check your connection and reload." : "You are offline and the engine is not cached yet. Connect once, then it works offline.";
    console.warn("engine init failed", e);
    return;
  }
  clearInterval(tick);
  S.engineReady = true; bar.classList.add("ready");
  F("footer-versions").textContent = `Engine v${S.meta.engine_version} · bundle ${S.meta.engine_bundle} · Pyodide ${S.meta.pyodide_version} · loaded in ${S.meta.load_seconds.toFixed(1)} s`;
  F("f-preset").innerHTML = `<option value="">Custom…</option>` + Object.keys(PRESETS).map((k) => `<option>${esc(k)}</option>`).join("");
  const sites = Object.keys(S.meta.sites);
  F("f-l-site").innerHTML = sites.map((s) => `<option>${esc(s)}</option>`).join("");
  F("f-site-check").innerHTML = `<option>None</option>` + sites.map((s) => `<option>${esc(s)}</option>`).join("");
  let cfg = structuredClone(S.meta.default_config);
  const shared = new URLSearchParams(location.search).get("m");
  if (shared) { try { cfg = mergeDefaults(JSON.parse(b64d(shared))); } catch { showSetupError("The shared link could not be read; showing the demo mission."); } }
  writeForm(cfg); F("f-preset").value = shared ? "" : "Sun-synchronous 600 km";
  setBusy(false, "");
  await runMission(cfg);                                // first result without any clicks
  setTimeout(cacheLoadedResources, 500);
}
boot();
