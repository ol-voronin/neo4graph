// Graph snapshot for the GitHub release "graph-latest" (read by web/api/_graph.js instead of ~16 MB from Neon per cold
// function instance). Writes into OUT_DIR (default ./graph-out):
//   graph_<YYYYMMDDHHMM>.json.gz  the graph exactly as _graph.js build() returns it (+ wm, the database clock of the build)
//   graph_manifest.json           { file, built, checked, full_at, wm, nodes, edges, size, sha }
// and prints changed=true|false (also to $GITHUB_OUTPUT). .github/workflows/graph.yml uploads both files.
//
// Modes:
//   default  incremental: take the published snapshot and merge the rows written since it (applyDelta, a few KB from
//            Neon). Falls back to a full build when there is no snapshot or its last full build is older than FULL_EVERY_H.
//   --full   rebuild everything from Neon (~16 MB of egress); the workflow does this once a day.
// When the merged graph is identical to the published one only the manifest is re-stamped (checked = now), so readers
// know the snapshot is current without a new upload.
//
// Env: DATABASE_URL; GRAPH_BASE_URL (default the public release URL); OUT_DIR; FULL_EVERY_H (default 24);
//      GRAPH_STUB=<module exporting `q`, a tagged-template query function> for tests without a database.
import { createHash } from "node:crypto";
import { gzipSync, gunzipSync } from "node:zlib";
import { mkdirSync, writeFileSync, appendFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

if (!process.env.DATABASE_URL) process.env.DATABASE_URL = "postgres://stub:stub@localhost/stub";  // _db.js needs a string at import
const { build, applyDelta } = await import("../api/_graph.js");
const q = process.env.GRAPH_STUB ? (await import(pathToFileURL(resolve(process.env.GRAPH_STUB)).href)).q : (await import("../api/_db.js")).sql;

const BASE = (process.env.GRAPH_BASE_URL || "https://github.com/ol-voronin/neo4graph/releases/download/graph-latest/").replace(/\/?$/, "/");
const OUT = process.env.OUT_DIR || "graph-out";
const FULL_EVERY = (+process.env.FULL_EVERY_H || 24) * 3600e3 - 30 * 60e3;  // half an hour of slack for cron jitter
const full = process.argv.includes("--full");

// content hash: what readers see, without the timestamps that change on every run, and independent of order (a delta
// appends new nodes at the end, a full build interleaves them; the same content must not be uploaded twice)
const sha = (g) => {
  const h = createHash("sha256");
  for (const s of g.nodes.map((n) => JSON.stringify(n)).sort()) h.update(s);
  for (const s of g.edges.map((e) => JSON.stringify({ ...e, msgs: [...e.msgs].sort() })).sort()) h.update(s);
  return h.update(JSON.stringify(g.sources)).digest("hex").slice(0, 16);
};

async function published() {
  try {
    const r = await fetch(BASE + "graph_manifest.json", { cache: "no-store" });
    if (!r.ok) return null;
    const m = await r.json();
    const a = await fetch(BASE + m.file);
    if (!a.ok) return null;
    return { m, g: JSON.parse(gunzipSync(Buffer.from(await a.arrayBuffer())).toString("utf8")) };
  } catch (e) {
    console.warn("no published snapshot:", e.message);
    return null;
  }
}

const t0 = Date.now();
const prev = await published();  // from GitHub, not Neon; also used to skip uploading an identical full build
let g, mode;
if (full || !prev || !prev.g.wm || Date.now() - Date.parse(prev.m.full_at || prev.g.full_at || 0) > FULL_EVERY) {
  mode = "full";
  g = await build(q);
} else {
  mode = "delta";
  if (prev.m.wm > prev.g.wm) prev.g.wm = prev.m.wm;  // unchanged runs re-stamp only the manifest: keep the delta window short
  g = await applyDelta(prev.g, q);
  console.log(`delta rows since ${prev.g.wm}: ${g.delta_rows}`);
  delete g.delta_rows;
}
const hash = sha(g);
const now = new Date().toISOString();
mkdirSync(OUT, { recursive: true });
let changed = true, manifest;
if (prev && prev.m.sha === hash) {
  changed = false;  // same content: re-stamp the manifest only, keep pointing at the published file
  manifest = { ...prev.m, checked: now, wm: g.wm, full_at: g.full_at };
} else {
  const stamp = now.replace(/[-:T]/g, "").slice(0, 14);  // YYYYMMDDHHMMSS
  const file = `graph_${stamp}.json.gz`;
  const gz = gzipSync(JSON.stringify(g), { level: 9 });
  writeFileSync(join(OUT, file), gz);
  manifest = { file, built: g.built, checked: now, full_at: g.full_at, wm: g.wm, nodes: g.nodes.length, edges: g.edges.length, size: gz.length, sha: hash };
}
writeFileSync(join(OUT, "graph_manifest.json"), JSON.stringify(manifest, null, 1));
console.log(`${mode}: ${g.nodes.length} nodes, ${g.edges.length} edges, ${changed ? "new file " + manifest.file + " (" + manifest.size + " B gz)" : "unchanged"}, ${Date.now() - t0} ms`);
console.log(`changed=${changed}`);
if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, `changed=${changed}\nfile=${manifest.file}\n`);
