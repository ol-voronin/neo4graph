import { sql, json } from "./_db.js";

// GET /api/case?num=991/1234/21  -> decisions with full metadata (+ texts flag), or prefix list with ?prefix=991/12
export default async function handler(req, res) {
  const url = new URL(req.url, "http://x");
  const num = url.searchParams.get("num");
  const prefix = url.searchParams.get("prefix");
  try {
    if (prefix) {
      const rows = await sql`SELECT cause_num, count(*)::int AS n, min(adjudication_date) AS d0, max(adjudication_date) AS d1
                             FROM court_decisions WHERE cause_num LIKE ${prefix + "%"} GROUP BY cause_num ORDER BY cause_num LIMIT 12`;
      return json(res, 200, { cases: rows });
    }
    if (!num) return json(res, 400, { error: "num or prefix required" });
    const rows = await sql`SELECT doc_id, cause_num, court_code, court, judgment_form, justice_kind, category, adjudication_date, receipt_date,
                           date_publ, judge, doc_url, status, year, (text IS NOT NULL) AS has_text
                           FROM court_decisions WHERE cause_num = ${num} ORDER BY adjudication_date`;
    const log = await sql`SELECT kind, steps, created_at FROM query_log WHERE query = ${num} ORDER BY created_at DESC LIMIT 1`;
    const queue = await sql`SELECT status, created_at, started_at, finished_at FROM query_queue WHERE query = ${num} ORDER BY created_at DESC LIMIT 1`;
    return json(res, 200, { cause: num, decisions: rows, last_run: log[0] || null, queue: queue[0] || null });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
