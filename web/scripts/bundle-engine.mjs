// Packs the Python engine (orbital_intelligence/) and the JSON bridge (oi_bridge/) into one JSON file
// that the Pyodide worker writes into its virtual filesystem. The Python files are copied unchanged.
// Also self-hosts the world map so the ground-track map works offline. Needs only Node (no Python).
import { readFileSync, writeFileSync, mkdirSync, readdirSync, copyFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const root = join(web, "..");
const files = {};
for (const pkg of ["orbital_intelligence", "oi_bridge"]) {
  for (const f of readdirSync(join(root, pkg)).filter((f) => f.endsWith(".py")).sort()) {
    files[`${pkg}/${f}`] = readFileSync(join(root, pkg, f), "utf8");
  }
}
const hash = createHash("sha256").update(JSON.stringify(files)).digest("hex").slice(0, 12);
mkdirSync(join(web, "public", "engine"), { recursive: true });
writeFileSync(join(web, "public", "engine", "engine.json"), JSON.stringify({ hash, files }));
mkdirSync(join(web, "public", "topojson"), { recursive: true });
copyFileSync(join(web, "node_modules", "sane-topojson", "dist", "world_110m.json"), join(web, "public", "topojson", "world_110m.json"));
console.log(`engine bundle ${hash}: ${Object.keys(files).length} Python files`);
