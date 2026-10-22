import { OBLASTS, oblastOf } from "./_text.js";

// Places for /api/search: the oblasts (+ Kyiv city) and every settlement of the KATOTTG codifier (geo/gazetteer_ua.json,
// ~17.7k names, 0.5 MB). The gazetteer lives outside web/ (the Vercel root), so it is fetched once per warm instance from the
// public repo; until it arrives, or when the fetch fails, only oblasts are matched.
const URL_ = process.env.GAZETTEER_URL || "https://raw.githubusercontent.com/ol-voronin/neo4graph/main/geo/gazetteer_ua.json";
const CAT = { K: "місто", M: "місто", X: "селище", C: "село", B: "район міста" };
const RANK = { K: 0, M: 1, X: 2, B: 3, C: 4 };
let gz = null, gzAt = 0, gzLoad = null;

export function loadGazetteer(ms = 2000) {
  if (gz && Date.now() - gzAt < 24 * 3600e3) return Promise.resolve(gz);
  gzLoad ??= fetch(URL_, { signal: AbortSignal.timeout(Math.max(ms, 1500)) }).then((r) => {
    if (!r.ok) throw new Error(`gazetteer: HTTP ${r.status}`);
    return r.json();
  }).then((d) => {
    gz = { regions: d.regions || [], places: d.places || {}, names: Object.keys(d.places || {}).sort() };
    gzAt = Date.now();
    return gz;
  }).finally(() => { gzLoad = null; });
  // the caller waits at most `ms`; the download keeps going and serves the next request
  return Promise.race([gzLoad, new Promise((_, no) => setTimeout(() => no(new Error("gazetteer: still loading")), ms))]);
}

const OBL_NAME = Object.fromEntries(OBLASTS.map(([slug, name]) => [slug, name]));
const oblLabel = (slug) => (slug === "kyiv-city" ? "м. Київ" : slug === "krym" ? "АР Крим" : slug === "sevastopol" ? "м. Севастополь" : (OBL_NAME[slug] || slug) + " обл.");

// q -> [{id: place:<name>, type: place, label, sub, region, tier}]: oblasts, then cities before villages, exact names before
// prefixes. Ids follow /api/graph's virtual place nodes (place:<lower-case name>: nodes whose region / court / address mention it).
export async function findPlaces(q, { limit = 12, wait = 2000 } = {}) {
  const k = String(q || "").toLowerCase().replace(/[’ʼ`]/g, "'").replace(/\s+/g, " ").trim();
  if (k.length < 2) return [];
  const out = [];
  for (const [slug, name] of OBLASTS) {
    const nm = name.toLowerCase(), city = slug === "kyiv-city";
    const hit = nm.startsWith(k) || slug.startsWith(k) || (k.length >= 4 && !/\s/.test(k) && oblastOf(k)?.[0] === slug);
    if (!hit) continue;
    out.push({ id: "place:" + (city ? "київ" : nm + " область"), type: "place", label: city ? "Київ" : name + " область",
      sub: city ? "місто зі спецстатусом" : "область", region: slug, tier: nm === k || (city && k === "київ") ? 0 : 1, rank: city ? 0 : -1 });
  }
  const g = await loadGazetteer(wait).catch(() => null);
  if (g) {
    // binary search for the first name >= k, then walk the prefix range
    let lo = 0, hi = g.names.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (g.names[mid] < k) lo = mid + 1; else hi = mid; }
    for (let i = lo; i < g.names.length && g.names[i].startsWith(k) && out.length < 300; i++) {
      const name = g.names[i];
      const where = String(g.places[name]).split(",").map((c) => /^(\d+)([A-Z])$/.exec(c)).filter((m) => m && g.regions[+m[1]])
        .map((m) => ({ slug: g.regions[+m[1]], cat: m[2] })).sort((a, b) => (RANK[a.cat] ?? 5) - (RANK[b.cat] ?? 5));
      if (!where.length) continue;
      const label = name.replace(/(^|[\s-])(\p{L})/gu, (_, a, b) => a + b.toUpperCase());
      const obl = [...new Set(where.map((w) => oblLabel(w.slug)))];
      out.push({ id: "place:" + name, type: "place", label,
        sub: `${CAT[where[0].cat] || "населений пункт"} · ${obl.slice(0, 3).join(", ")}${obl.length > 3 ? ` і ще ${obl.length - 3}` : ""}`,
        region: where[0].slug, tier: name === k ? 0 : 1, rank: RANK[where[0].cat] ?? 5 });
    }
  }
  const seen = new Set();
  return out.sort((a, b) => a.tier - b.tier || a.rank - b.rank || a.label.length - b.label.length)
    .filter((x) => !seen.has(x.id) && seen.add(x.id)).slice(0, limit).map(({ rank, ...x }) => x);
}
