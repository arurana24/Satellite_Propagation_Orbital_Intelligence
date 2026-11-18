/* Module Web Worker (create with {type: "module"}): runs the unchanged Python engine in Pyodide so heavy runs never freeze the page.
   Protocol  in: {id, cmd: "init"|"run"|"analyse"|"figure"|"meta", payload}
            out: {type: "progress", stage, pct, label} | {id, ok: true, result} | {id, ok: false, error} */
const PYODIDE_VERSION = "314.0.8";
const INDEX_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
let py = null;
let ready = null;

const progress = (stage, pct, label) => postMessage({ type: "progress", stage, pct, label });

async function init() {
  const t0 = performance.now();
  progress("core", 5, "Downloading Python runtime (Pyodide)…");
  // Module worker + dynamic import (importScripts() of the CDN script is refused by current Chromium).
  const { loadPyodide } = await import(INDEX_URL + "pyodide.mjs");
  py = await loadPyodide({ indexURL: INDEX_URL });
  progress("numpy", 35, "Loading NumPy…");
  await py.loadPackage("numpy");
  progress("scipy", 55, "Loading SciPy (largest download, ~13 MB)…");
  await py.loadPackage("scipy");
  progress("engine", 85, "Installing the orbital engine…");
  const bundle = await (await fetch("engine/engine.json", { cache: "no-cache" })).json();
  for (const [path, src] of Object.entries(bundle.files)) {
    const dir = "/home/pyodide/" + path.split("/")[0];
    try { py.FS.mkdirTree(dir); } catch (_) { /* exists */ }
    py.FS.writeFile("/home/pyodide/" + path, src);
  }
  py.runPython("import sys; sys.path.insert(0, '/home/pyodide'); from oi_bridge import api");
  progress("warmup", 95, "Warming up…");
  const meta = JSON.parse(py.runPython("api.meta()"));
  meta.engine_bundle = bundle.hash;
  meta.pyodide_version = PYODIDE_VERSION;
  meta.load_seconds = (performance.now() - t0) / 1000;
  progress("done", 100, "Ready");
  return meta;
}

function call(fn, payload) {
  py.globals.set("_oi_arg", JSON.stringify(payload ?? {}));
  const out = py.runPython(`api.${fn}(_oi_arg)`);
  const res = JSON.parse(out);
  if (!res.ok) throw Object.assign(new Error(res.error.message), { kind: res.error.kind, pytype: res.error.type });
  return res;
}

self.onmessage = async (ev) => {
  const { id, cmd, payload } = ev.data;
  try {
    if (cmd === "init") {
      ready = ready || init();
      postMessage({ id, ok: true, result: await ready });
      return;
    }
    await ready;
    const t0 = performance.now();
    let result;
    if (cmd === "run") result = call("run_mission", payload);
    else if (cmd === "analyse") result = call("analyse", payload);
    else if (cmd === "figure") result = call("figure", payload);
    else throw new Error(`unknown command ${cmd}`);
    result.compute_seconds = (performance.now() - t0) / 1000;
    postMessage({ id, ok: true, result });
  } catch (e) {
    postMessage({ id, ok: false, error: { message: String(e.message || e), kind: e.kind || "engine", type: e.pytype || e.name } });
  }
};
