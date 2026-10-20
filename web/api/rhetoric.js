import { json, query } from "./_db.js";

// GET /api/rhetoric?group=country|side|actor&country=&side=&actor=&weeks=26
// Rhetoric level over time: statements by week (all / threats) per country, side or named actor, with each group's track
// record (share of resolved statements that came true), plus the share of threat-flavoured posts in the channels by country.
// One actor, several spellings. Role suffixes ("Синєгубов (Харківська ОДА)", "Філашкін, голова …") are cut first; then
// explicit rules (institutions and the most quoted people, any script), then a person heuristic: 1–3 capitalised words
// (initials allowed), no abbreviations or institutional words -> country + surname ("М.В.Захарова" = "Марія Захарова").
const NM = "trim(regexp_replace(coalesce(nullif(trim(actor_name), ''), actor_side), '\\s*[,(].*$', ''))";
const RULES = [
  ["(mid_russia|^мид\\M|мзс (рф|росі)|министерств\\w* иностранн)", "МЗС Росії"],
  ["(минобороны|міноборони (рф|росі)|министерств\\w* обороны|mod_russia)", "Міноборони РФ"],
  ["(generalstaff|генштаб|генеральн\\w* штаб)", "Генштаб ЗСУ"],
  ["(офіс президента|офис президента)", "Офіс Президента"],
  ["^(kremlin|kremlin_ru|кремль)$", "Кремль"],
  ["(zelensk|зеленськ|зеленск|^президент україни$|^президент украины$)", "UA:Зеленський"],
  ["(putin|путін|путин)", "RU:Путін"],
  ["(trump|трамп)", "US:Трамп"],
  ["(lavrov|лавров)", "RU:Лавров"],
  ["(zakharova|захарова)", "RU:Захарова"],
  ["(peskov|песков|пєсков)", "RU:Пєсков"],
];
const PERSON = "^([[:upper:]]\\.\\s?)*[[:upper:]][[:lower:]''’ʼ-]+(\\s+([[:upper:]]\\.\\s?)*[[:upper:]][[:lower:]''’ʼ-]+){0,2}$";
const NOT_PERSON = "(україн|росі|росс|област|штаб|сил|міністер|министер|канал|посол|голова|президент|служб|офіс|офис|уряд|рада|прес|військ|военн|адміністрац|администрац|командуван|генерал)";
const ACTOR_KEY = `CASE ${RULES.map(([re, name]) => `WHEN ${NM} ~* '${re}' THEN '${name}'`).join(" ")}
  WHEN ${NM} ~ '${PERSON}' AND ${NM} !~* '${NOT_PERSON}'
    THEN split_part(actor_side, '_', 1) || ':' || regexp_replace(regexp_replace(${NM}, '([[:upper:]]\\.\\s?)+', '', 'g'), '^.*\\s', '')
  ELSE ${NM} END`;
const KEY = { country: "split_part(actor_side, '_', 1)", side: "actor_side", actor: ACTOR_KEY };
// same keyword list as rule_features.r_threat (db/migrations/004_rule_features.sql)
const THREAT_RE = "(удар|ракет|обстріл|обстрел|знищ|уничтож|ядерн|орешник|орєшнік|oreshnik|масован|массирован|відплат|возмезд|ответн|ультиматум|retaliat|strike|missile|nuclear|annihilat|сотр[её]м|стерти)";

export default async function handler(req, res) {
  const u = new URL(req.url, "http://x");
  const group = KEY[u.searchParams.get("group")] ? u.searchParams.get("group") : "country";
  const side = u.searchParams.get("side") || "";
  const country = u.searchParams.get("country") || "";
  const actor = u.searchParams.get("actor") || "";
  const weeks = Math.min(104, Math.max(4, +(u.searchParams.get("weeks") || 26)));
  const key = KEY[group];
  const P = [weeks, side, "%" + actor + "%", actor, country];
  const S = `FROM statements WHERE ts_published >= now() - ($1::int * interval '1 week') AND ($2 = '' OR actor_side = $2) AND ($4 = '' OR actor_name ILIKE $3) AND ($5 = '' OR split_part(actor_side, '_', 1) = $5)`;
  try {
    const [top, series, types, channels] = await Promise.all([
      query(`SELECT ${key} AS k, ${group === "actor" ? `CASE WHEN ${key} LIKE '%:%' THEN coalesce(mode() WITHIN GROUP (ORDER BY ${NM}) FILTER (WHERE ${NM} ~ '${PERSON}' AND ${NM} ~ '\\s[[:upper:]][[:lower:]]'), split_part(${key}, ':', 2)) ELSE ${key} END` : key} AS label,
               bool_or(actor_name IS NOT NULL AND trim(actor_name) <> '') named, max(split_part(actor_side, '_', 1)) country, max(actor_side) side, count(*)::int n,
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
