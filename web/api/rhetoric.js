import { json, query } from "./_db.js";

// GET /api/rhetoric?group=country|side|actor&side=&actor=&weeks=26
// Rhetoric level over time: statements by week (all / threats) per country, side or named actor, with each group's track
// record (share of resolved statements that came true), plus the share of threat-flavoured posts in the channels by country.
const KEY = { country: "split_part(actor_side, '_', 1)", side: "actor_side", actor: "coalesce(nullif(actor_name, ''), actor_side)" };
// same keyword list as rule_features.r_threat (db/migrations/004_rule_features.sql)
const THREAT_RE = "(удар|ракет|обстріл|обстрел|знищ|уничтож|ядерн|орешник|орєшнік|oreshnik|масован|массирован|відплат|возмезд|ответн|ультиматум|retaliat|strike|missile|nuclear|annihilat|сотр[её]м|стерти)";

export default async function handler(req, res) {
  const u = new URL(req.url, "http://x");
  const group = KEY[u.searchParams.get("group")] ? u.searchParams.get("group") : "country";
  const side = u.searchParams.get("side") || "";
  const actor = u.searchParams.get("actor") || "";
  const weeks = Math.min(104, Math.max(4, +(u.searchParams.get("weeks") || 26)));
  const key = KEY[group];
  const P = [weeks, side, "%" + actor + "%", actor];
  const S = `FROM statements WHERE ts_published >= now() - ($1::int * interval '1 week') AND ($2 = '' OR actor_side = $2) AND ($4 = '' OR actor_name ILIKE $3)`;
  try {
    const [top, series, types, channels] = await Promise.all([
      query(`SELECT ${key} AS k, max(split_part(actor_side, '_', 1)) country, max(actor_side) side, count(*)::int n,
               count(*) FILTER (WHERE stype = 'threat')::int threats, count(*) FILTER (WHERE stype = 'promise')::int promises,
               count(*) FILTER (WHERE stype = 'claim_fact')::int claims, count(*) FILTER (WHERE stype = 'denial')::int denials,
               count(*) FILTER (WHERE status IN ('true', 'false', 'partial'))::int resolved,
               round((sum(CASE status WHEN 'true' THEN 1 WHEN 'partial' THEN 0.5 ELSE 0 END)
                      / nullif(count(*) FILTER (WHERE status IN ('true', 'false', 'partial')), 0))::numeric, 2)::float pct_true,
               max(ts_published)::date last_seen
             ${S} GROUP BY 1 ORDER BY n DESC LIMIT 25`, P),
      query(`SELECT ${key} AS k, date_trunc('week', ts_published)::date w, count(*)::int n, count(*) FILTER (WHERE stype = 'threat')::int threats
             ${S} GROUP BY 1, 2 ORDER BY 2`, P),
      query(`SELECT stype, count(*)::int n ${S} GROUP BY 1 ORDER BY 2 DESC`, P),
      // channel rhetoric: share of posts with threat vocabulary, by source country and week (all channels except OSINT feeds)
      query(`WITH m AS MATERIALIZED (
               SELECT date_trunc('week', m.ts_published)::date w, s.country, lower(m.text) x
               FROM messages m JOIN sources s ON s.id = m.source_id
               WHERE m.ts_published >= now() - ($1::int * interval '1 week') AND s.type <> 'osint' AND length(m.text) >= 40 AND s.country IN ('RU', 'UA'))
             SELECT w, country, count(*)::int posts, round(avg((x ~ $2)::int)::numeric, 3)::float threat_share FROM m GROUP BY 1, 2 ORDER BY 1`, [weeks, THREAT_RE]),
    ]);
    return json(res, 200, { group, weeks, top, series, types, channels });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
