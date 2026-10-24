import { sql, json, classify } from "./_db.js";

// POST   /api/queue {query, kind?}  -> enqueue a registry chain run (the cloud leg: queue.yml and the live loop, every
//                                      ~30 min; the UA leg: the local worker, within minutes)
// POST   /api/queue {retry: id}     -> put a failed / cancelled item back in the queue (both legs run again)
// DELETE /api/queue?id=N            -> cancel a queued item (a running chain is not interrupted)
// GET    /api/queue?q=...           -> status of one query; without q the newest 50 items (?status=queued|running|done|failed)
export default async function handler(req, res) {
  try {
    await sql`CREATE TABLE IF NOT EXISTS query_queue (id bigserial PRIMARY KEY, query text NOT NULL, kind text, status text NOT NULL DEFAULT 'queued',
              created_at timestamptz DEFAULT now(), started_at timestamptz, finished_at timestamptz, note text)`;
    if (req.method === "POST") {
      let body = "";
      for await (const chunk of req) body += chunk;
      const { query, kind, retry } = JSON.parse(body || "{}");
      if (retry) {
        const row = await sql`UPDATE query_queue SET status = 'queued', started_at = NULL, finished_at = NULL, note = NULL, ua_status = NULL, ua_note = NULL
                              WHERE id = ${+retry} AND status IN ('failed', 'cancelled', 'done') RETURNING id, query, status`;
        return json(res, row.length ? 200 : 409, row.length ? { queued: true, ...row[0] } : { error: "only a finished, failed or cancelled item can be retried" });
      }
      const q = (query || "").trim();
      if (q.length < 3 || q.length > 600) return json(res, 400, { error: "query length" });
      const open = await sql`SELECT id, status, created_at FROM query_queue WHERE query = ${q} AND status IN ('queued','running') LIMIT 1`;
      if (open.length) return json(res, 200, { queued: true, existing: true, ...open[0] });
      const k = kind || classify(q);
      const row = await sql`INSERT INTO query_queue(query, kind) VALUES (${q}, ${k}) RETURNING id, status, created_at`;
      return json(res, 200, { queued: true, kind: k, ...row[0] });
    }
    const url = new URL(req.url, "http://x");
    if (req.method === "DELETE") {
      const row = await sql`UPDATE query_queue SET status = 'cancelled', finished_at = now(), ua_status = coalesce(ua_status, 'cancelled')
                            WHERE id = ${+url.searchParams.get("id")} AND status = 'queued' RETURNING id, query, status`;
      return json(res, row.length ? 200 : 409, row.length ? row[0] : { error: "only a queued item can be cancelled" });
    }
    const q = url.searchParams.get("q") || "";
    const st = url.searchParams.get("status") || "";
    if (url.searchParams.get("pending") === "ua") {
      // work list for the local registry worker (Ukrainian IP): items the cloud chain cannot complete
      if (!process.env.INGEST_TOKEN || req.headers["x-ingest-token"] !== process.env.INGEST_TOKEN) return json(res, 401, { error: "bad token" });
      await sql`ALTER TABLE query_queue ADD COLUMN IF NOT EXISTS ua_status text`;
      const rows = await sql`SELECT id, query, kind, status, created_at FROM query_queue WHERE ua_status IS NULL AND kind IN ('case','person','org','text','edrpou','rnokpp','code')
                             ORDER BY created_at LIMIT 5`;
      return json(res, 200, { items: rows });
    }
    const rows = await sql`SELECT id, query, kind, status, created_at, started_at, finished_at, note, ua_status, ua_note, ua_done_at,
                           (SELECT count(*)::int FROM query_queue x WHERE x.status = 'queued' AND x.created_at < query_queue.created_at) ahead
                           FROM query_queue WHERE (${q} = '' OR query = ${q}) AND (${st} = '' OR status = ${st})
                           ORDER BY created_at DESC LIMIT ${q ? 20 : 50}`.catch(() =>
                           sql`SELECT id, query, kind, status, created_at, started_at, finished_at, note FROM query_queue WHERE (${q} = '' OR query = ${q}) ORDER BY created_at DESC LIMIT 20`);
    const worker = await sql`SELECT seen_at, note FROM worker_status WHERE name = 'ua'`.catch(() => []);
    return json(res, 200, { items: rows, worker: worker[0] || null });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
