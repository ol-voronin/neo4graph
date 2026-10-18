import { sql, json } from "./_db.js";

// GET /api/factcheck?side=RU_official&status=false&limit=50 -> checked statements + per-actor summary
export default async function handler(req, res) {
  const u = new URL(req.url, "http://x");
  const side = u.searchParams.get("side") || "";
  const status = u.searchParams.get("status") || "";
  const q = u.searchParams.get("q") || "";
  const lim = Math.min(200, +(u.searchParams.get("limit") || 60));
  try {
    const items = await sql`SELECT s.id, s.ts_published, s.actor_side, s.actor_name, s.stype, s.quote, s.predicted_event, s.target_region, s.horizon_days,
        s.status, s.resolved_at, s.resolver_note, s.evidence_ids, m.url, m.source_id
      FROM statements s JOIN messages m ON m.id = s.message_id
      WHERE (${side} = '' OR s.actor_side = ${side}) AND (${status} = '' OR s.status = ${status})
        AND (${q} = '' OR s.quote ILIKE ${"%" + q + "%"} OR s.actor_name ILIKE ${"%" + q + "%"} OR s.predicted_event ILIKE ${"%" + q + "%"})
      ORDER BY (s.status <> 'pending') DESC, s.resolved_at DESC NULLS LAST, s.ts_published DESC LIMIT ${lim}`;
    const ids = [...new Set(items.flatMap((i) => i.evidence_ids || []))].filter((x) => !x.startsWith("gt:"));
    const ev = ids.length ? await sql`SELECT id, source_id, ts_published, url, left(regexp_replace(text,'\s+',' ','g'), 240) snippet FROM messages WHERE id = ANY(${ids})` : [];
    const summary = await sql`SELECT actor_side, actor_name,
        count(*)::int n, count(*) FILTER (WHERE status='true')::int confirmed, count(*) FILTER (WHERE status='false')::int refuted,
        count(*) FILTER (WHERE status='partial')::int partial, count(*) FILTER (WHERE status='unresolvable')::int unverifiable, count(*) FILTER (WHERE status='pending')::int pending
      FROM statements GROUP BY 1,2 HAVING count(*) FILTER (WHERE status <> 'pending') > 0 ORDER BY (count(*) FILTER (WHERE status IN ('true','false'))) DESC LIMIT 40`;
    const sides = await sql`SELECT actor_side, count(*)::int n, count(*) FILTER (WHERE status='true')::int confirmed, count(*) FILTER (WHERE status='false')::int refuted,
        count(*) FILTER (WHERE status='partial')::int partial, count(*) FILTER (WHERE status='unresolvable')::int unverifiable FROM statements GROUP BY 1 ORDER BY n DESC`;
    return json(res, 200, { items, evidence: ev, by_actor: summary, by_side: sides });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
