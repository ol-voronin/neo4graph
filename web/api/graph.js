import { sql, json } from "./_db.js";
import { getGraph, overview, around } from "./_graph.js";

// GET /api/graph                                -> overview: the most connected nodes and the edges between them (light)
// GET /api/graph?around=<id>&depth=2&limit=150  -> that node's neighbourhood, ranked by distance then degree, capped
//     &fresh=<unique>                           -> rebuild first (a registry run just added data); a unique value misses the CDN
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));

export default async function handler(req, res) {
  const p = new URL(req.url, "http://x").searchParams;
  try {
    const g = await getGraph(p.has("fresh"));
    const id = p.get("around");
    if (!id) return json(res, 200, overview(g, clamp(+p.get("n") || 300, 50, 800)));
    const r = around(g, id, clamp(+p.get("depth") || 2, 1, 3), clamp(+p.get("limit") || 150, 10, 600));
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
