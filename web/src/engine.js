// Thin promise wrapper around the Pyodide Web Worker (public/pyodide-worker.js).
export class Engine {
  constructor(onProgress) {
    this.worker = new Worker(new URL("/pyodide-worker.js", location.href), { type: "module" });
    this.pending = new Map();
    this.next = 0;
    this.onProgress = onProgress;
    this.worker.onmessage = (ev) => {
      const m = ev.data;
      if (m.type === "progress") { this.onProgress?.(m); return; }
      const p = this.pending.get(m.id);
      if (!p) return;
      this.pending.delete(m.id);
      if (m.ok) p.resolve(m.result);
      else p.reject(Object.assign(new Error(m.error.message), m.error));
    };
    this.worker.onerror = (ev) => {
      for (const p of this.pending.values()) p.reject(new Error(ev.message || "worker failed"));
      this.pending.clear();
    };
  }
  call(cmd, payload) {
    const id = ++this.next;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ id, cmd, payload });
    });
  }
  init() { return this.call("init"); }
  run(config) { return this.call("run", config); }
  analyse(req) { return this.call("analyse", req); }
  figure(req) { return this.call("figure", req); }
}
