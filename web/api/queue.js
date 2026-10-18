import { sql, json, classify } from "./_db.js";

// POST /api/queue {query, kind?}  -> enqueue a registry chain run (picked up by the GitHub workflow every 10 min)
// GET  /api/queue?q=...           -> status
export default async function handler(req, res) {
  try {
    await sql`CREATE TABLE IF NOT EXISTS query_queue (id bigserial PRIMARY KEY, query text NOT NULL, kind text, status text NOT NULL DEFAULT 'queued',
              created_at timestamptz DEFAULT now(), started_at timestamptz, finished_at timestamptz, note text)`;
    if (req.method === "POST") {
      let body = "";
      for await (const chunk of req) body += chunk;
      const { query, kind } = JSON.parse(body || "{}");
      const q = (query || "").trim();
      if (q.length < 3 || q.length > 200) return json(res, 400, { error: "query length" });
      const open = await sql`SELECT id, status, created_at FROM query_queue WHERE query = ${q} AND status IN ('queued','running') LIMIT 1`;
      if (open.length) return json(res, 200, { queued: true, existing: true, ...open[0] });
      const k = kind || classify(q);
      const row = await sql`INSERT INTO query_queue(query, kind) VALUES (${q}, ${k}) RETURNING id, status, created_at`;
      return json(res, 200, { queued: true, kind: k, ...row[0] });
    }
    const url = new URL(req.url, "http://x");
    const q = url.searchParams.get("q") || "";
    const rows = await sql`SELECT id, query, kind, status, created_at, started_at, finished_at, note FROM query_queue
                           WHERE (${q} = '' OR query = ${q}) ORDER BY created_at DESC LIMIT 20`;
    return json(res, 200, { items: rows });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
