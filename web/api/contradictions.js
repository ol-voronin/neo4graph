import { sql, json } from "./_db.js";

// GET /api/contradictions?side=&q=  -> position changes with both quotes, plus per-actor counts
export default async function handler(req, res) {
  const u = new URL(req.url, "http://x");
  const side = u.searchParams.get("side") || "";
  const q = u.searchParams.get("q") || "";
  try {
    const items = await sql`SELECT c.id, c.actor_side, c.actor_name, c.kind, c.topic, c.explanation, c.confidence,
        a.ts_published a_date, a.quote a_quote, a.stype a_type, ma.url a_url,
        b.ts_published b_date, b.quote b_quote, b.stype b_type, mb.url b_url,
        e.event_date e_date, e.event_type e_type, e.details e_details
      FROM contradictions c
      JOIN statements a ON a.id = c.a_statement JOIN messages ma ON ma.id = a.message_id
      LEFT JOIN statements b ON b.id = c.b_statement LEFT JOIN messages mb ON mb.id = b.message_id
      LEFT JOIN extracted_events e ON e.id = c.b_event
      WHERE (${side} = '' OR c.actor_side = ${side}) AND (${q} = '' OR c.actor_name ILIKE ${"%" + q + "%"} OR c.topic ILIKE ${"%" + q + "%"} OR a.quote ILIKE ${"%" + q + "%"})
      ORDER BY c.confidence DESC, b.ts_published DESC NULLS LAST LIMIT 100`;
    const by_actor = await sql`SELECT c.actor_side, c.actor_name, count(*)::int n_changes,
        (SELECT count(*)::int FROM statements s WHERE s.actor_side = c.actor_side AND coalesce(s.actor_name,'') = coalesce(c.actor_name,'')) n_statements
      FROM contradictions c GROUP BY 1,2 ORDER BY n_changes DESC LIMIT 40`;
    return json(res, 200, { items, by_actor });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
