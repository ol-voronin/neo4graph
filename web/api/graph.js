import { sql, json } from "./_db.js";
import { getGraph, overview, around } from "./_graph.js";
import { lawyerRows, lawyerGraph } from "./_lawyers.js";

// GET /api/graph                                -> overview: the most connected nodes and the edges between them (light)
// GET /api/graph?around=<id>&depth=2&limit=150  -> that node's neighbourhood, ranked by distance then degree, capped
//     &fresh=<unique>                           -> merge rows written since the graph snapshot first (a registry run just added
//                                                  data; a few KB from Neon, see _graph.js); a unique value misses the CDN
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const soft = (p, ms, dflt) => Promise.race([Promise.resolve(p), new Promise((_, no) => setTimeout(() => no(new Error("timeout")), ms))]).catch(() => dflt);

// lawyers of a case / cases of a lawyer straight from person_case_links (rebuilt by src/person_cases.py more often than the
// daily graph snapshot): merged into the neighbourhood so the probable lawyer edges show up as soon as the table has them
async function withLawyers(r, id) {
  const kind = id.slice(0, id.indexOf(":"));
  if (kind !== "case" && kind !== "person") return r;
  const L = lawyerGraph(await soft(lawyerRows(kind === "case" ? { cases: [id] } : { persons: [id] }), 2000, []));
  if (!L.edges.length) return r;
  r = r || { center: id, nodes: [], edges: [], ev: {}, total: 0, shown: 0 };
  const have = new Set(r.nodes.map((n) => n.id)), ek = new Set(r.edges.map((e) => e.source + "|" + e.target + "|" + e.rel));
  for (const n of L.nodes) if (!have.has(n.id)) { r.nodes.push({ ...n, deg: n.id === id ? L.edges.length : 1 }); have.add(n.id); r.total++; r.shown++; }
  for (const e of L.edges) if (!ek.has(e.source + "|" + e.target + "|" + e.rel)) r.edges.push(e);
  return r;
}

export default async function handler(req, res) {
  const p = new URL(req.url, "http://x").searchParams;
  try {
    const g = await getGraph(p.has("fresh"));
    const id = p.get("around");
    if (!id) return json(res, 200, overview(g, clamp(+p.get("n") || 300, 50, 800)));
    const r = await withLawyers(around(g, id, clamp(+p.get("depth") || 2, 1, 3), clamp(+p.get("limit") || 150, 10, 600)), id);
    if (r) return json(res, 200, r);
    if (id.startsWith("post:")) {  // a post found by text search that is not (yet) evidence for anything in the graph
      const m = (await sql`SELECT source_id s, ts_published::date t, url u, left(regexp_replace(text, '[[:space:]]+', ' ', 'g'), 260) x FROM messages WHERE id = ${id.slice(5)}`)[0];
      if (m) return json(res, 200, { center: id, nodes: [{ id, type: "post", label: (m.x || "").slice(0, 70) + "…", src: m.s, date: m.t, url: m.u, text: m.x, deg: 0 }], edges: [], ev: {}, total: 1, shown: 1 });
    }
    return json(res, 200, { center: id, nodes: [], edges: [], ev: {}, total: 0, shown: 0, missing: true });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
