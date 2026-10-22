import { json, query } from "./_db.js";

// GET /api/mood?weeks=26
// Tone of posts toward the tracked topics (ТЦК і мобілізація, закони, корупція, полонені, обміни, перемир'я), from the labeling
// prompt v3 field topics:[{topic, object, stance −2..2}] (view topic_mood, migration 020). Ukrainian channels give the mood in
// Ukraine; Russian channels show how the same topic is framed for Russian audiences, so the two are kept apart.
// series: by topic, source country and week (posts, mean stance, share negative / positive); groups: by source type;
// objects: what the posts are about (last 8 weeks, Ukrainian sources), with their mean stance.
const TOPICS = ["tcc", "law", "corruption", "pow", "exchange", "ceasefire"];
const CTRY = "CASE WHEN country IN ('UA', 'RU') THEN country ELSE 'other' END";

export default async function handler(req, res) {
  const u = new URL(req.url, "http://x");
  const weeks = Math.min(104, Math.max(4, +(u.searchParams.get("weeks") || 26)));
  const W = "ts_published >= now() - ($1::int * interval '1 week')";
  const AGG = `count(*)::int n, round(avg(stance)::numeric, 2)::float avg, round(avg((stance < 0)::int)::numeric, 3)::float neg,
    round(avg((stance > 0)::int)::numeric, 3)::float pos`;
  try {
    const [series, groups, objects, cov] = await Promise.all([
      query(`SELECT topic, ${CTRY} country, date_trunc('week', ts_published)::date::text w, ${AGG} FROM topic_mood WHERE ${W} GROUP BY 1, 2, 3 ORDER BY 3`, [weeks]),
      query(`SELECT topic, ${CTRY} country, coalesce(source_type, 'other') stype, ${AGG} FROM topic_mood WHERE ${W} GROUP BY 1, 2, 3 ORDER BY n DESC`, [weeks]),
      query(`SELECT topic, object, n, avg FROM (
               SELECT topic, lower(object) object, count(*)::int n, round(avg(stance)::numeric, 2)::float avg,
                      row_number() OVER (PARTITION BY topic ORDER BY count(*) DESC) r
               FROM topic_mood WHERE country = 'UA' AND object IS NOT NULL AND ts_published >= now() - interval '8 weeks' GROUP BY 1, 2) x
             WHERE r <= 8 AND n >= 2 ORDER BY topic, n DESC`, []),
      query(`SELECT count(DISTINCT message_id)::int labeled, min(ts_published)::date::text first FROM topic_mood`, []),
    ]);
    return json(res, 200, { weeks, since: cov[0]?.first || null, topics: TOPICS, series, groups, objects, coverage: cov[0] || { labeled: 0, first: null } });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
