import { parquetMetadataAsync, parquetReadObjects } from "hyparquet";
import { compressors } from "hyparquet-compressors";

// Read-only access to the lake's search index (src/lake.py build_index) straight from the GitHub release "lake-latest".
// Nothing is copied into Neon: each file is sorted by its lookup key, registry_manifest.json lists the first/last key of
// every row group, and a lookup fetches the Parquet footer once per warm instance plus one or two row groups (~0.3-0.8 MB)
// with HTTP range requests.
const BASE = (process.env.LAKE_BASE_URL || "https://github.com/ol-voronin/neo4graph/releases/download/lake-latest/").replace(/\/?$/, "/");
const MANIFEST_TTL = 10 * 60e3;  // the index is rebuilt nightly
const URL_TTL = 20 * 60e3;       // GitHub's signed asset URLs live ~1 h
const MAX_GROUPS = 3;            // at most ~49k rows read per lookup

let manifest = null, manifestAt = 0;
const files = new Map();  // name -> { url, urlAt, meta, built }

export async function getManifest() {
  if (manifest && Date.now() - manifestAt < MANIFEST_TTL) return manifest;
  const r = await fetch(BASE + "registry_manifest.json");
  if (!r.ok) throw new Error(`registry manifest: HTTP ${r.status}`);
  const m = await r.json();
  if (!manifest || manifest.built !== m.built) files.clear();
  manifest = m; manifestAt = Date.now();
  return m;
}

// github.com/.../releases/download/... answers 302 to a signed blob URL; resolve it once instead of on every range request.
async function assetUrl(name, fresh = false) {
  const f = files.get(name) || files.set(name, {}).get(name);
  if (!fresh && f.url && Date.now() - f.urlAt < URL_TTL) return f.url;
  const r = await fetch(BASE + name, { method: "HEAD", redirect: "manual" });
  f.url = r.headers.get("location") || BASE + name;
  f.urlAt = Date.now();
  return f.url;
}

function rangeBuffer(name, byteLength) {
  return {
    byteLength,
    async slice(start, end) {
      const headers = { Range: `bytes=${start}-${(end ?? byteLength) - 1}` };
      let r = await fetch(await assetUrl(name), { headers });
      if (r.status === 403 || r.status === 404) r = await fetch(await assetUrl(name, true), { headers });  // signed URL expired
      // never fall back to a whole-file download: these files are hundreds of MB
      if (r.status !== 206) throw new Error(`range request for ${name}: HTTP ${r.status}`);
      return r.arrayBuffer();
    },
  };
}

async function readRows(fileKey, rowStart, rowEnd) {
  const m = await getManifest();
  const spec = m.files[fileKey];
  const file = rangeBuffer(spec.file, spec.size);
  const f = files.get(spec.file) || files.set(spec.file, {}).get(spec.file);
  // one footer fetch per file and warm instance, shared by parallel lookups
  f.meta ||= parquetMetadataAsync(file).catch((e) => { f.meta = null; throw e; });
  return parquetReadObjects({ file, metadata: await f.meta, rowStart, rowEnd, compressors, rowFormat: "object" });
}

// Row groups whose key range can hold keys starting with `prefix` (or equal to it when exact).
export function groupSpan(spec, prefix, exact = false) {
  const hi = exact ? prefix : prefix + "￿";
  const g = spec.groups;
  let lo = 0, up = g.length;  // first group whose last key >= prefix
  while (lo < up) { const mid = (lo + up) >> 1; if (g[mid][2] < prefix) lo = mid + 1; else up = mid; }
  let end = lo;
  while (end < g.length && g[end][1] <= hi) end++;
  return [lo, end];  // groups [lo, end)
}

// All rows whose key starts with (or equals) `prefix`, in key order, reading at most MAX_GROUPS row groups.
export async function lookup(fileKey, prefix, { exact = false } = {}) {
  const m = await getManifest();
  const spec = m.files[fileKey];
  if (!spec || !prefix) return { rows: [], truncated: false };
  const [g0, g1] = groupSpan(spec, prefix, exact);
  if (g0 >= g1) return { rows: [], truncated: false };
  const last = Math.min(g1, g0 + MAX_GROUPS);
  const rowEnd = last < spec.groups.length ? spec.groups[last][0] : spec.rows;
  const rows = await readRows(fileKey, spec.groups[g0][0], rowEnd);
  const col = spec.key;
  const hit = rows.filter((r) => (exact ? r[col] === prefix : r[col]?.startsWith(prefix)));
  return { rows: hit, truncated: g1 > last };
}

// The same normalisation as src/lake.py search_key/core_key; the alphabet maps and legal forms come from the manifest.
export function searchKey(s, m) {
  let out = "";
  for (const ch of String(s || "").toUpperCase()) { const i = m.latin.indexOf(ch); out += i >= 0 ? m.cyr[i] : ch; }
  return out.replace(/['’ʼ`´‘]/g, "").replace(/[^0-9A-ZА-ЯЄІЇҐ]+/g, " ").trim();
}

export function coreKey(k, m) {
  const re = new RegExp("^(?:(?:" + m.forms.join("|") + ") )+");
  return (k + " ").replace(re, "").trim() || k;
}
