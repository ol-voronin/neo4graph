import { sql, query, json } from "./_db.js";
import { dossier, withTimeout } from "./_intent.js";
import { hierarchy, trend, score, SCORED, MIN_N, K } from "./_trust.js";

// GET /api/factcheck?side=RU_official&status=false&limit=50          -> checked statements + per-actor summary (list view)
// GET /api/factcheck?level=country|group[&id=RU|ru_officials]           -> trust scores by country / group (+ detail with id)
// GET /api/factcheck?level=person&id=ru:lavrov                         -> person page: said, did, refuted, self-contradictions, trust
// GET /api/factcheck?level=find&name=Зеленський Володимир Олександрович -> {id, label, n} for the graph «Профіль» link
// GET /api/factcheck?level=overview                                    -> dashboard: totals, topic × country, types, months, refuted
// &topic=pow|exchange|ceasefire|tcc|law|corruption (statements.topic, migration 020) or promise (stype) narrows every level.
// Levels need migration 019 (actors, actor_aliases, actor_groups, statements_x). Trust formula: _trust.js, docs/factcheck.md.
export const TOPICS = ["pow", "exchange", "ceasefire", "tcc", "law", "corruption"];
const topicOf = (t) => (TOPICS.includes(t) || t === "promise" ? t : "");
// '' = all statements; 'promise' is a statement type, the rest are statements.topic
const TOPIC_SQL = (p) => `(${p} = '' OR (${p} = 'promise' AND stype = 'promise') OR topic = ${p})`;
// pending splits in two: q = waits for a check (facts, due or future forecasts); x = never checked (no checkable event: opinion,
// demand without a deadline; extraction set resolvable = false)
const CHECKABLE = `(resolvable OR stype IN ('claim_fact', 'denial'))`;
const VERD = `count(*)::int n, count(*) FILTER (WHERE status = 'true')::int t, count(*) FILTER (WHERE status = 'false')::int f,
    count(*) FILTER (WHERE status = 'partial')::int p, count(*) FILTER (WHERE status = 'unresolvable')::int u,
    count(*) FILTER (WHERE status = 'pending' AND ${CHECKABLE})::int q, count(*) FILTER (WHERE status = 'pending' AND NOT ${CHECKABLE})::int x`;
const COUNTS = `SELECT country, grp, actor_id, mode() WITHIN GROUP (ORDER BY actor_name) label, count(*)::int "all",
    count(*) FILTER (WHERE status = 'true' AND stype = ANY($1))::int t, count(*) FILTER (WHERE status = 'false' AND stype = ANY($1))::int f,
    count(*) FILTER (WHERE status = 'partial' AND stype = ANY($1))::int p, count(*) FILTER (WHERE status = 'unresolvable')::int u,
    count(*) FILTER (WHERE status = 'pending' AND ${CHECKABLE})::int q
  FROM statements_x WHERE ${TOPIC_SQL("$2")} GROUP BY 1, 2, 3`;
const COL = { country: "country", group: "grp", person: "actor_id" };
const soft = (p, ms, d = null) => withTimeout(Promise.resolve(p), ms).catch(() => d);

async function tree(topic = "") {
  const [rows, groups, actors] = await Promise.all([query(COUNTS, [SCORED, topic]), sql`SELECT id, name_uk, country, ord FROM actor_groups ORDER BY ord`,
    sql`SELECT id, name_uk, name_en, kind, role_uk, grp, graph_name FROM actors`]);
  const h = hierarchy(rows);
  const A = new Map(actors.map((a) => [a.id, a])), Gm = new Map(groups.map((g) => [g.id, g]));
  h.groups.forEach((g) => Object.assign(g, { name: Gm.get(g.id)?.name_uk || g.id, ord: Gm.get(g.id)?.ord ?? 99 }));
  h.groups.sort((a, b) => a.ord - b.ord);
  h.people.forEach((p) => { const a = A.get(p.id); if (a) Object.assign(p, { label: a.name_uk, kind: a.kind, role: a.role_uk, curated: true }); });
  h.people.sort((a, b) => b.score.n - a.score.n || b.all - a.all);
  return h;
}

// monthly counts for one country / group / actor -> cumulative trust after each month
async function monthly(level, id, prior, topic = "") {
  const r = await query(`SELECT to_char(date_trunc('month', ts_published), 'YYYY-MM') m,
      count(*) FILTER (WHERE status = 'true' AND stype = ANY($2))::int t, count(*) FILTER (WHERE status = 'false' AND stype = ANY($2))::int f,
      count(*) FILTER (WHERE status = 'partial' AND stype = ANY($2))::int p, count(*)::int said
    FROM statements_x WHERE ${COL[level]} = $1 AND ${TOPIC_SQL("$3")} GROUP BY 1 ORDER BY 1`, [id, SCORED, topic]);
  const tr = trend(r, prior);
  return tr.map((x, i) => ({ ...x, said: r[i].said }));
}

const ST = `id, ts_published, stype, quote, predicted_event, target_region, status, resolved_at, resolver_note, evidence_ids, url, source_id, actor_name, actor_id`;
async function evidence(items) {
  const ids = [...new Set(items.flatMap((i) => i.evidence_ids || []))].filter((x) => !x.startsWith("gt:"));
  return ids.length ? sql`SELECT id, source_id, ts_published, url, left(regexp_replace(text, '[[:space:]]+', ' ', 'g'), 260) snippet FROM messages WHERE id = ANY(${ids})` : [];
}

async function level(lv, id, topic = "") {
  const h = await tree(topic);
  if (!id) return { level: lv, topic, min_n: MIN_N, k: K, all: h.all, countries: h.countries.sort((a, b) => b.all - a.all), groups: h.groups, people: h.people.slice(0, 400) };
  const node = (lv === "country" ? h.countries : h.groups).find((x) => x.id === id);
  if (!node) return { level: lv, id, error: "немає заяв" };
  const inside = (p) => (lv === "country" ? p.country === id : p.grp === id);
  const [tr, refuted] = await Promise.all([monthly(lv, id, lv === "country" ? h.all.mean : h.countries.find((c) => c.id === node.country)?.score.mean ?? h.all.mean, topic),
    query(`SELECT ${ST} FROM statements_x WHERE ${COL[lv]} = $1 AND status IN ('false', 'partial') AND ${TOPIC_SQL("$2")} ORDER BY resolved_at DESC NULLS LAST LIMIT 15`, [id, topic])]);
  return { level: lv, id, topic, min_n: MIN_N, k: K, node, all: h.all, groups: lv === "country" ? h.groups.filter((g) => g.country === id) : undefined,
    people: h.people.filter(inside).slice(0, 60), trend: tr, refuted, evidence: await evidence(refuted) };
}

async function person(id) {
  const h = await tree();
  const p = h.people.find((x) => x.id === id);
  const [actor] = await sql`SELECT a.*, g.name_uk group_name, (SELECT array_agg(pattern) FROM actor_aliases WHERE actor_id = a.id) aliases FROM actors a LEFT JOIN actor_groups g ON g.id = a.grp WHERE a.id = ${id}`;
  if (!p && !actor) return { level: "person", id, error: "немає такої людини в заявах" };
  const grp = h.groups.find((g) => g.id === (p?.grp || actor?.grp));
  // what they did: extracted events naming them, by alias regex or (no curated row) the surname from the id
  const pats = actor?.aliases?.length ? actor.aliases : [id.includes(":~") ? id.split(":~")[1].slice(0, 6) : id];
  const surname = actor ? actor.name_uk.split(" ").slice(-1)[0] : p?.label?.split(" ").slice(-1)[0];
  const dname = actor?.graph_name || (actor?.kind === "person" ? actor.name_uk.split(" ").reverse().join(" ") : null) || (p && /\s/.test(p.label || "") ? p.label.split(" ").reverse().join(" ") : surname);
  const [said, refuted, contra, events, types, tr, dos] = await Promise.all([
    query(`SELECT ${ST} FROM statements_x WHERE actor_id = $1 ORDER BY ts_published DESC LIMIT 200`, [id]),
    query(`SELECT ${ST} FROM statements_x WHERE actor_id = $1 AND status IN ('false', 'partial') ORDER BY ts_published DESC LIMIT 50`, [id]),
    query(`SELECT c.id, c.kind, c.topic, c.explanation, c.confidence, a.ts_published a_date, a.quote a_quote, a.stype a_type, ma.url a_url,
        b.ts_published b_date, b.quote b_quote, b.stype b_type, mb.url b_url, e.event_date::text e_date, e.event_type e_type, e.details e_details
      FROM contradictions c JOIN statements a ON a.id = c.a_statement JOIN messages ma ON ma.id = a.message_id
      LEFT JOIN statements b ON b.id = c.b_statement LEFT JOIN messages mb ON mb.id = b.message_id LEFT JOIN extracted_events e ON e.id = c.b_event
      WHERE c.actor_id = $1 OR a.actor_id = $1 ORDER BY a.ts_published DESC LIMIT 50`, [id]),
    query(`SELECT e.id, e.event_type, e.event_date::text event_date, e.region, e.actor, e.details, e.confidence, m.url, m.source_id FROM extracted_events e JOIN messages m ON m.id = e.message_id
      WHERE e.actor ~* ANY($1) AND e.event_type <> 'strike' ORDER BY e.event_date DESC NULLS LAST LIMIT 40`, [pats]),
    query(`SELECT stype, count(*)::int n, count(*) FILTER (WHERE status = 'true')::int t, count(*) FILTER (WHERE status = 'false')::int f,
        count(*) FILTER (WHERE status = 'partial')::int p, count(*) FILTER (WHERE status = 'unresolvable')::int u FROM statements_x WHERE actor_id = $1 GROUP BY 1 ORDER BY 2 DESC`, [id]),
    p ? monthly("person", id, grp?.score.mean ?? h.all.mean) : [],
    // registry roles, court decisions, cases and news posts about them (the chat's entity dossier, without the graph)
    actor?.kind === "institution" || !dname ? null : soft(dossier("person:" + dname.toLowerCase(), { graph: false }), 9000),
  ]);
  const arr = (x) => (Array.isArray(x) ? x : []);  // a dossier part that failed or timed out comes back as {error}
  const did = dos ? { roles: arr(dos.roles), cases: arr(dos.cases), media_cases: arr(dos.media_cases), decisions: arr(dos.decisions), edr_roles: arr(dos.edr_roles), wanted: arr(dos.wanted),
    posts: dos.posts?.items ? dos.posts : null, registry_hits: arr(dos.registry_hits), web: arr(dos.web) } : null;
  return { level: "person", id, min_n: MIN_N, k: K, actor: actor || null, label: actor?.name_uk || p?.label || id, graph_name: dname,
    group: grp ? { id: grp.id, name: grp.name, score: grp.score } : null, country: p?.country || actor?.country,
    score: p?.score || null, types, trend: tr, said, refuted, evidence: await evidence(refuted), contradictions: contra, events, did };
}

// dashboard: verdict counts overall, by topic, topic × country, statement type and month; trust per topic and per cell with
// the same shrinkage as the levels (prior = all statements' mean); latest refuted statements
async function overview(topic) {
  const T = TOPIC_SQL("$1");
  // scored counts (demands excluded, as in the levels): st / sf / sp
  const SC = `count(*) FILTER (WHERE status = 'true' AND stype = ANY($2))::int st, count(*) FILTER (WHERE status = 'false' AND stype = ANY($2))::int sf,
    count(*) FILTER (WHERE status = 'partial' AND stype = ANY($2))::int sp`;
  const [totals, byTopic, matrix, stypes, months, refuted] = await Promise.all([
    query(`SELECT ${VERD}, ${SC} FROM statements_x WHERE ${T}`, [topic, SCORED]),
    query(`SELECT topic, ${VERD}, ${SC} FROM statements_x WHERE $1::text IS NOT NULL GROUP BY 1`, [topic, SCORED]),
    query(`SELECT country, topic, ${VERD}, ${SC} FROM statements_x WHERE ${T} GROUP BY 1, 2`, [topic, SCORED]),
    query(`SELECT stype, ${VERD} FROM statements_x WHERE ${T} GROUP BY 1 ORDER BY n DESC`, [topic]),
    query(`SELECT to_char(date_trunc('month', ts_published), 'YYYY-MM') m, country, ${VERD} FROM statements_x
      WHERE ${T} AND ts_published >= now() - interval '18 months' GROUP BY 1, 2 ORDER BY 1`, [topic]),
    query(`SELECT id, ts_published, actor_name, actor_id, country, topic, stype, quote, predicted_event, resolver_note, url, status
      FROM statements_x WHERE status IN ('false', 'partial') AND ${T} ORDER BY resolved_at DESC NULLS LAST LIMIT 15`, [topic]),
  ]);
  // prior for every cell: all statements pooled under a flat Beta(1,1), as at the top of hierarchy()
  const pool = byTopic.reduce((a, r) => ({ t: a.t + r.st, f: a.f + r.sf, p: a.p + r.sp }), { t: 0, f: 0, p: 0 });
  const m0 = score(pool, 0.5, 2).mean;
  const out = ({ st, sf, sp, ...r }) => ({ ...r, score: score({ t: st, f: sf, p: sp, u: r.u, q: r.q }, m0) });
  const { st, sf, sp, ...tot } = totals[0];
  return { topic, min_n: MIN_N, totals: tot, topics: byTopic.map(out), matrix: matrix.map(out), stypes, monthly: months, recent_refuted: refuted };
}

// graph label / any spelling -> canonical actor with statements
async function find(name) {
  name = (name || "").trim();
  if (name.length < 3) return { id: null };
  const words = name.split(/\s+/).filter((w) => w.length >= 3);
  let [r] = await query(`SELECT a.actor_id id FROM actor_aliases a WHERE lower($1) ~* a.pattern ORDER BY length(a.pattern) DESC LIMIT 1`, [name]);
  if (!r) for (const w of [words[0], words.at(-1)].filter(Boolean)) {
    // surname-first graph labels and name-first quotes: try both ends, stem to 6 letters for case forms
    [r] = await query(`SELECT actor_id id FROM statements WHERE actor_id IS NOT NULL AND actor_name ~* $1 GROUP BY 1 ORDER BY count(*) DESC LIMIT 1`,
      ["\\m" + w.toLowerCase().slice(0, 6).replace(/[^\p{L}'’-]/gu, "")]);
    if (r) break;
  }
  if (!r) return { id: null };
  const [c] = await sql`SELECT count(*)::int n, count(*) FILTER (WHERE status IN ('true', 'false', 'partial'))::int resolved,
      coalesce((SELECT name_uk FROM actors WHERE id = ${r.id}), mode() WITHIN GROUP (ORDER BY actor_name)) label FROM statements WHERE actor_id = ${r.id}`;
  return { id: c.n ? r.id : null, ...c };
}

export default async function handler(req, res) {
  const u = new URL(req.url, "http://x");
  const lv = u.searchParams.get("level") || "";
  const id = u.searchParams.get("id") || "";
  const topic = topicOf(u.searchParams.get("topic") || "");
  try {
    if (lv === "overview") return json(res, 200, await overview(topic));
    if (lv === "country" || lv === "group") return json(res, 200, await level(lv, id, topic));
    if (lv === "person") return json(res, 200, await person(id));
    if (lv === "find") return json(res, 200, await find(u.searchParams.get("name")));
    const side = u.searchParams.get("side") || "";
    const status = u.searchParams.get("status") || "";
    const q = u.searchParams.get("q") || "";
    const lim = Math.min(200, +(u.searchParams.get("limit") || 60));
    const items = await sql`SELECT s.id, s.ts_published, s.actor_side, s.actor_name, s.stype, s.quote, s.predicted_event, s.target_region, s.horizon_days,
        s.status, s.resolved_at, s.resolver_note, s.evidence_ids, m.url, m.source_id
      FROM statements s JOIN messages m ON m.id = s.message_id
      WHERE (${side} = '' OR s.actor_side = ${side}) AND (${status} = '' OR s.status = ${status})
        AND (${topic} = '' OR (${topic} = 'promise' AND s.stype = 'promise') OR s.topic = ${topic})
        AND (${q} = '' OR s.quote ILIKE ${"%" + q + "%"} OR s.actor_name ILIKE ${"%" + q + "%"} OR s.predicted_event ILIKE ${"%" + q + "%"})
      ORDER BY (s.status <> 'pending') DESC, s.resolved_at DESC NULLS LAST, s.ts_published DESC LIMIT ${lim}`;
    const ev = await evidence(items);
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
