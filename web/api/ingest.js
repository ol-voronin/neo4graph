import { sql, json } from "./_db.js";

// POST /api/ingest  (header x-ingest-token) — results from the local registry worker (local/ua_worker.py), which runs on a
// machine with a Ukrainian IP because reyestr.court.gov.ua and the NAZK declarations API are not reachable from the cloud.
// body: {kind: "decisions"|"hits"|"links"|"queue", rows: [...]} or {kind:"queue", queue_id, status, note}
export async function ensure() {
  await sql`CREATE TABLE IF NOT EXISTS registry_hits (id bigserial PRIMARY KEY, query text NOT NULL, qkind text, source text NOT NULL, ref text NOT NULL,
            title text, snippet text, url text, payload jsonb, created_at timestamptz DEFAULT now(), UNIQUE (query, source, ref))`;
  await sql`CREATE TABLE IF NOT EXISTS analyst_links (id bigserial PRIMARY KEY, src_kind text NOT NULL, src_name text NOT NULL, dst_kind text NOT NULL, dst_name text NOT NULL,
            relation text NOT NULL, note text, created_at timestamptz DEFAULT now(), UNIQUE (src_kind, src_name, dst_kind, dst_name, relation))`;
  await sql`ALTER TABLE query_queue ADD COLUMN IF NOT EXISTS ua_status text`;
  await sql`ALTER TABLE query_queue ADD COLUMN IF NOT EXISTS ua_note text`;
  await sql`ALTER TABLE query_queue ADD COLUMN IF NOT EXISTS ua_done_at timestamptz`;
}

export default async function handler(req, res) {
  try {
    if (req.method !== "POST") return json(res, 405, { error: "POST only" });
    if (!process.env.INGEST_TOKEN || req.headers["x-ingest-token"] !== process.env.INGEST_TOKEN) return json(res, 401, { error: "bad token" });
    let body = "";
    for await (const chunk of req) body += chunk;
    const { kind, rows = [], queue_id, status, note } = JSON.parse(body || "{}");
    await ensure();
    let n = 0;
    if (kind === "decisions") {
      for (const r of rows) {
        const year = r.adjudication_date ? Number(String(r.adjudication_date).slice(0, 4)) : null;
        await sql`INSERT INTO court_decisions(doc_id, cause_num, court, judgment_form, justice_kind, adjudication_date, receipt_date, judge, doc_url, status, year, text, text_fetched_at)
                  VALUES (${String(r.doc_id)}, ${r.cause_num || null}, ${r.court || null}, ${r.judgment_form || null}, ${r.justice_kind || null}, ${r.adjudication_date || null},
                          ${r.receipt_date || null}, ${r.judge || null}, ${r.doc_url || "https://reyestr.court.gov.ua/Review/" + r.doc_id}, ${"1"}, ${year}, ${r.text || null},
                          ${r.text ? new Date() : null})
                  ON CONFLICT (doc_id) DO UPDATE SET text = COALESCE(EXCLUDED.text, court_decisions.text), text_fetched_at = COALESCE(EXCLUDED.text_fetched_at, court_decisions.text_fetched_at),
                          cause_num = COALESCE(court_decisions.cause_num, EXCLUDED.cause_num), court = COALESCE(court_decisions.court, EXCLUDED.court),
                          judge = COALESCE(court_decisions.judge, EXCLUDED.judge), judgment_form = COALESCE(court_decisions.judgment_form, EXCLUDED.judgment_form)`;
        n++;
      }
    } else if (kind === "hits") {
      for (const r of rows) {
        await sql`INSERT INTO registry_hits(query, qkind, source, ref, title, snippet, url, payload)
                  VALUES (${r.query}, ${r.qkind || null}, ${r.source}, ${String(r.ref)}, ${r.title || null}, ${r.snippet || null}, ${r.url || null}, ${JSON.stringify(r.payload || {})})
                  ON CONFLICT (query, source, ref) DO UPDATE SET title = EXCLUDED.title, snippet = EXCLUDED.snippet, url = EXCLUDED.url, payload = EXCLUDED.payload`;
        n++;
      }
    } else if (kind === "links") {
      for (const r of rows) {
        await sql`INSERT INTO analyst_links(src_kind, src_name, dst_kind, dst_name, relation, note) VALUES (${r.src_kind}, ${r.src_name}, ${r.dst_kind}, ${r.dst_name}, ${r.relation}, ${r.note || null})
                  ON CONFLICT (src_kind, src_name, dst_kind, dst_name, relation) DO UPDATE SET note = EXCLUDED.note`;
        n++;
      }
    } else if (kind === "queue") {
      await sql`UPDATE query_queue SET ua_status = ${status || "done"}, ua_note = ${note || null}, ua_done_at = now() WHERE id = ${queue_id}`;
      n = 1;
    } else return json(res, 400, { error: "unknown kind" });
    return json(res, 200, { ok: true, n });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
