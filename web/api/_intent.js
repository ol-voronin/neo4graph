import { sql, query } from "./_db.js";
import { getCaps, sources, plan } from "./search.js";
import { byName, byCode, card } from "./registry.js";
import { getManifest } from "./_lake.js";
import { getGraph, around, norm, isPlaceholder } from "./_graph.js";
import { ukVariants, queryWords, stem, isStop, wordRe, oblastOf, oblastTextRe, dateOf, kyivToday, addDays } from "./_text.js";

// Chat tools over the whole base: what a question is about (resolve), everything about one object (dossier),
// word search in the post corpus, strikes ground truth and Russian-side rhetoric before a date.
export const withTimeout = (p, ms) => Promise.race([p, new Promise((_, no) => setTimeout(() => no(new Error(`timeout ${ms / 1000} s`)), ms))]);
const soft = (p, ms, dflt = null) => withTimeout(Promise.resolve(p), ms).catch((e) => (dflt && typeof dflt === "object" && !Array.isArray(dflt) ? { ...dflt, error: String(e.message || e) } : dflt));
export const CAUSE_ANY = /\d{1,4}\/\d{1,6}\/\d{2}(?:-[\p{L}\d/]+)?/u;
export const CASE_FULL = /^\d{1,4}\/\d{1,6}\/\d{2}(?:-[\p{L}\d/]+)?$/u;
export const FOP_RE = /(?:^|\s)фоп\s+([\p{L}'’ʼ-]+(?:\s+[\p{L}'’ʼ-]+){1,2})/iu;
const LEGAL = /(^|\s)(тов|пат|прат|ат|тдв|дп|кп|пп|ооо|зао|оао|ао)(?=\s|$)|[«»"“”]/i;
const STRIKE = /удар|обстр|атак|ракет|шахед|герань|гераней|бпла|дрон|прил[іеё]т|вибух|взрыв|тривог|тревог|каб(?![а-яії])|бомб|масован|массирован/i;
const RHET = /російськ|русск|росі[йя]|z-?канал|воєнкор|военкор|мілблог|милблог|пропаганд|канал|риторик|погроз|угроз|писали|пишут|писав|кремл|рибар|рыбар/i;
const TYPE_RANK = { case: 0, person: 0, org: 1, registry: 1, place: 2, channel: 2, post: 3 };
const dt = (d) => { if (!d) return null; const x = d instanceof Date ? d : new Date(d); return isNaN(x) ? String(d).slice(0, 10) : new Date(x.getTime() - x.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
const clip = (s, n) => (s && s.length > n ? s.slice(0, n) + "…" : s);

// ---- the question -> what it names. Proper-noun runs are looked up in every name source, in Ukrainian spellings too ----
function spansOf(text) {
  const toks = norm(text).replace(/[?!,.:;()]+/g, " ").split(" ").filter(Boolean);
  const spans = [];
  let cur = [];
  const flush = () => { if (cur.length) spans.push(cur.join(" ")); cur = []; };
  toks.forEach((w, i) => {
    const bare = w.replace(/[«»"“”„]/g, "");
    const name = /^\p{Lu}/u.test(bare) && !isStop(bare) && !/^\d/.test(bare) && !(i === 0 && /^(де|хто|що|які|як|коли|чому|где|кто|что|какие|как|когда|почему|покажи|знайди|найди|розкажи|расскажи)$/i.test(bare));
    if (name) cur.push(bare); else flush();
  });
  flush();
  if (!spans.length) {
    // lower-case input ("дело о миндиче"): the words left after dropping stop words, if few
    const rest = toks.filter((w) => !isStop(w) && w.length >= 3 && !/^\d/.test(w) && !STRIKE.test(w));
    if (rest.length && rest.length <= 3) spans.push(rest.join(" "));
  }
  return spans.slice(0, 3);
}

function spellings(span) {
  const words = span.toLowerCase().split(" ");
  const stemmed = words.map(stem).join(" ");
  const out = [...new Set([...ukVariants(stemmed, 3), ...(stemmed !== words.join(" ") ? ukVariants(words.join(" "), 2) : [])])];
  return out.filter((v) => v.replace(/\s/g, "").length >= 3).slice(0, 5);
}

const rankSort = (a, b) => a.tier - b.tier || (TYPE_RANK[a.type] ?? 3) - (TYPE_RANK[b.type] ?? 3) || (b.sim || 0) - (a.sim || 0) || (b.n || 0) - (a.n || 0);

export async function resolve(q) {
  const text = norm(q);
  const out = { q: text, kinds: [], spans: [], spellings: [], candidates: [], best: null };
  const C = await soft(getCaps(), 3000, { names: false, trgm: false });
  const jobs = [], lake = [];
  const cause = text.match(CAUSE_ANY)?.[0];
  const code = text.match(/(?<!\d)(\d{8}|\d{10})(?!\d)/)?.[1];
  const fop = text.match(FOP_RE)?.[1];
  const channel = text.match(/(?:@|t\.me\/|tg:)([A-Za-z0-9_]{4,})/)?.[1];
  const obl = oblastOf(text);
  const date = dateOf(text);
  if (cause) { out.kinds.push("case"); jobs.push(...plan(sources(cause, C), "case", cause)); }
  if (code) {
    out.kinds.push(code.length === 8 ? "edrpou" : "rnokpp");
    jobs.push(...plan(sources(code, C), "code", code));
    lake.push(soft(byCode(code), 6000, { rows: [] }));
  }
  if (channel) { out.kinds.push("channel"); jobs.push(...plan(sources(channel, C), "channel", channel)); }
  if (fop) { out.kinds.push("fop"); lake.push(soft(getManifest().then((m) => byName(fop, m, true)), 6000, { rows: [] })); }
  if (obl) out.kinds.push("place");
  if (STRIKE.test(text)) out.kinds.push("strikes");
  if (RHET.test(text)) out.kinds.push("rhetoric");
  const spans = cause || code ? [] : spansOf(fop ? text.replace(FOP_RE, " ") + " " + fop : text);
  for (const s of spans) {
    // a span that is only the oblast (Харкову) is a place, not a name to look up
    if (obl && s.split(" ").length === 1 && oblastOf(s)?.[0] === obl[0]) continue;
    const sp = spellings(s);
    out.spans.push(s); out.spellings.push(...sp);
    for (const v of sp) jobs.push(...plan(sources(v, C), "all", v));
    if (LEGAL.test(s) || (/\s/.test(s) && !fop)) lake.push(soft(getManifest().then((m) => byName(s, m, false)), 6000, { rows: [] }));
  }
  if (!out.kinds.length || out.spans.length) out.kinds.push(LEGAL.test(text) ? "org" : out.spans.length ? "name" : "topic");
  const lists = await Promise.all(jobs.map((p) => soft(p, 6000, [])));
  const m = lake.length ? await soft(getManifest(), 3000, null) : null;
  const lakeRows = (await Promise.all(lake)).flatMap((x) => (x.rows || []).slice(0, 5));
  const seen = new Set(), cands = [];
  const push = (c) => { if (!c?.id || seen.has(c.id) || isPlaceholder(c.label)) return; seen.add(c.id); cands.push(c); };
  if (obl) push({ id: "place:" + obl[0], type: "place", label: obl[1], sub: "область (" + obl[0] + ")", tier: spans.length ? 1 : 0 });
  lists.flat().sort(rankSort).forEach(push);
  for (const r of lakeRows) {
    const c = m ? card(r, m) : r;
    push({ id: c.id, type: c.kind === "fop" && c.id?.startsWith("person:") ? "person" : "registry", label: c.name, sub: [c.code_type && c.code ? `${c.code_type} ${c.code}` : "", c.stan, c.region].filter(Boolean).join(" · "), tier: 1, lake: true });
  }
  out.candidates = cands.sort(rankSort).slice(0, 12).map(({ sim, ...c }) => c);
  const named = out.candidates.filter((c) => c.type !== "place" && c.type !== "post");
  out.best = (named.find((c) => c.tier <= 1) || (obl ? out.candidates.find((c) => c.type === "place") : null) || named[0] || null)?.id || null;
  if (obl) out.region = { id: obl[0], name: obl[1] };
  if (date) out.date = date;
  out.today = kyivToday();
  out.next = [
    out.best && !out.best.startsWith("place:") ? `entity_dossier(id="${out.best}")` : null,
    out.kinds.includes("strikes") ? `strikes(region="${obl?.[0] || ""}", date_from="${date || out.today}")` : null,
    out.kinds.includes("rhetoric") ? `rhetoric_before(date="${date || out.today}"${obl ? `, region="${obl[0]}"` : ""})` : null,
    !out.candidates.length ? "search_corpus(q=…)" : null].filter(Boolean);
  return out;
}

// ---- word search over posts: every word must occur (as a word start, any spelling), newest first; LIMIT n+1 -> "more" ----
export async function corpus({ q = "", words, since, until, country, source, limit = 15, region } = {}) {
  const ws = (words || queryWords(q)).filter((w) => w.length >= 3 || /^\d+$/.test(w)).slice(0, 5);
  const o = region ? oblastOf(region) : null;
  if (!ws.length && !o) return { error: "немає слів для пошуку (лише службові слова)" };
  const lim = Math.min(40, limit || 15);
  const P = [since || null, until || null, country || null, source || null];
  const cond = ws.map((w) => (P.push(wordRe(w)), `m.text ~ $${P.length}`));
  if (o) { P.push(oblastTextRe(o)); cond.push(`lower(m.text) ~ $${P.length}`); }
  P.push(ws.length ? wordRe(ws[0]) : oblastTextRe(o));
  const rows = await query(`SELECT m.id, m.source_id, s.name src, s.country, m.ts_published, m.url, m.threat_level,
      left(regexp_replace(coalesce(substring(m.text from '.{0,120}' || $${P.length} || '.{0,200}'), m.text), '[[:space:]]+', ' ', 'g'), 320) snippet
    FROM messages m LEFT JOIN sources s ON s.id = m.source_id
    WHERE ${cond.join(" AND ")} AND ($1::timestamptz IS NULL OR m.ts_published >= $1::timestamptz) AND ($2::timestamptz IS NULL OR m.ts_published < $2::timestamptz)
      AND ($3::text IS NULL OR s.country = $3::text) AND ($4::text IS NULL OR m.source_id = $4::text)
    ORDER BY m.ts_published DESC LIMIT ${lim + 1}`, P);
  return { words: ws, found: Math.min(rows.length, lim), more: rows.length > lim,
    items: rows.slice(0, lim).map((r) => ({ id: r.id, date: dt(r.ts_published), ts: r.ts_published, source: r.source_id, src: r.src, country: r.country, url: r.url, threat_level: r.threat_level, snippet: r.snippet })) };
}

// ---- one court case: decisions in the base; queue the case when none ----
export async function caseCard(num, enqueue) {
  const key = num.toLowerCase();
  const [sum] = await sql`SELECT count(*)::int n, min(adjudication_date)::text d0, max(adjudication_date)::text d1, count(DISTINCT court)::int courts,
                                 string_agg(DISTINCT court, ' · ') court_list, string_agg(DISTINCT judge, ' · ') judges, count(text)::int texts,
                                 max(category) category, max(justice_kind) justice_kind FROM court_decisions WHERE (lower(cause_num) = ${key} OR lower(cause_num) LIKE ${key + "-%"})`;
  if (!sum.n) return { found: 0, ...(enqueue ? await enqueue(num, "case") : {}) };
  const [forms, timeline] = await Promise.all([
    sql`SELECT judgment_form f, count(*)::int n FROM court_decisions WHERE (lower(cause_num) = ${key} OR lower(cause_num) LIKE ${key + "-%"}) GROUP BY 1 ORDER BY 2 DESC`,
    sql`SELECT doc_id, adjudication_date::text d, judgment_form f, court, judge, doc_url FROM court_decisions WHERE (lower(cause_num) = ${key} OR lower(cause_num) LIKE ${key + "-%"}) ORDER BY adjudication_date DESC NULLS LAST LIMIT 40`,
  ]);
  return { found: sum.n, summary: sum, forms, timeline };
}

// ---- everything about one object, gathered in parallel; each part has its own time cap and fails alone ----
const nameOf = (id, node) => {
  const key = id.slice(id.indexOf(":") + 1);
  return norm(node?.label || key).replace(/^(ЄДР \d+ · )/, "").replace(/^Справа\s+/i, "");
};
const core = (s) => norm(s).replace(/[«»"“”„]/g, " ").replace(/^(тов|пат|прат|ат|тдв|дп|кп|пп|фоп|державне підприємство|приватне підприємство|товариство з обмеженою відповідальністю)\s+/i, "").replace(/\s+/g, " ").trim();

export async function dossier(id, { enqueue, graph = true } = {}) {
  id = norm(id);
  const i = id.indexOf(":"), kind = id.slice(0, i), key = id.slice(i + 1);
  if (i < 1 || !key) return { error: "id має вигляд person:…, org:…, case:…, registry:<ЄДРПОУ>, place:<slug>, channel:<id>" };
  // the in-memory graph (cold build: seconds) loads alongside the queries; names come from the id, not from the graph
  const gp = graph ? soft(getGraph(), 8000, null) : Promise.resolve(null);  // graph:false — the person page needs no neighbourhood
  const out = { id, kind, label: (kind === "place" && oblastOf(key)?.[1]) || key };
  const P = {};
  if (kind === "case" && CASE_FULL.test(key)) {
    const num = key;
    P.court = caseCard(num, enqueue);
    P.media = sql`SELECT name_norm, role, position, left(quote, 200) quote, url, confidence FROM media_links WHERE lower(cause_num) = ${key} ORDER BY confidence DESC LIMIT 15`;
    P.posts = query(`SELECT m.id, m.source_id source, m.ts_published, m.url, left(regexp_replace(m.text, '[[:space:]]+', ' ', 'g'), 300) snippet FROM messages m
                     WHERE m.text LIKE $1 ORDER BY m.ts_published DESC LIMIT 16`, ["%" + num + "%"]).then((r) => ({ found: Math.min(15, r.length), more: r.length > 15, items: r.slice(0, 15).map((x) => ({ ...x, date: dt(x.ts_published) })) }));
  } else if (kind === "place") {
    const o = oblastOf(key);
    const to = kyivToday();
    if (o) {
      P.strikes = strikes({ region: o[0], date_from: addDays(to, -7), date_to: to });
      P.posts = corpus({ region: o[0], since: addDays(to, -3), limit: 15 });
    }
  } else if (kind === "channel") {
    P.source = sql`SELECT id, name, country, lang, type, region, tier FROM sources WHERE id = ${key}`.then((r) => r[0] || null);
    P.activity = sql`SELECT count(*)::int n, min(ts_published) first, max(ts_published) last, count(*) FILTER (WHERE ts_published > now() - interval '7 days')::int week FROM messages WHERE source_id = ${key}`.then((r) => r[0]);
    P.posts = query(`SELECT id, ts_published, url, threat_level, event_kind, left(regexp_replace(text, '[[:space:]]+', ' ', 'g'), 300) snippet FROM messages WHERE source_id = $1 ORDER BY ts_published DESC LIMIT 15`, [key])
      .then((r) => ({ found: r.length, items: r.map((x) => ({ ...x, source: key, date: dt(x.ts_published) })) }));
  } else if (kind === "post") {
    P.post = sql`SELECT m.id, m.source_id, s.name src, s.country, m.ts_published, m.url, m.text, m.threat_level, m.event_kind FROM messages m LEFT JOIN sources s ON s.id = m.source_id WHERE m.id = ${key}`.then((r) => r[0] || null);
    P.statements = sql`SELECT actor_side, actor_name, stype, quote, predicted_event, target_region, status, resolver_note FROM statements WHERE message_id = ${key}`;
    P.events = sql`SELECT event_type, event_date, region, missiles, drones, guided_bombs, killed, injured, details FROM extracted_events WHERE message_id = ${key}`;
  } else {
    // a named thing: person, organisation, ЄДР record, ФОП card
    let name = nameOf(id);
    if (kind === "registry" && /^\d{8}$/.test(key)) {
      const r = await soft(sql`SELECT edrpou, name, short_name, stan, founders, signers, beneficiaries, authorized_capital FROM edr_orgs WHERE edrpou = ${key}`.then((x) => x[0]), 4000, null);
      if (r) { out.record = { ...r, founders: (r.founders || []).slice(0, 15), signers: (r.signers || []).slice(0, 10), beneficiaries: (r.beneficiaries || []).slice(0, 10) }; name = r.short_name || r.name; }
      else {
        const hit = (await soft(byCode(key), 6000, { rows: [] })).rows?.[0];
        const m = hit ? await soft(getManifest(), 3000, null) : null;
        if (hit && m) { out.record = card(hit, m); name = hit.name; }
      }
      P.payments = sql`SELECT payer_name, count(*)::int n, sum(amount)::float total, min(trans_date)::date::text d0, max(trans_date)::date::text d1 FROM public_payments WHERE recipt_edrpou = ${key} GROUP BY 1 ORDER BY 3 DESC LIMIT 10`;
    }
    if (["fop", "fopx", "orgx", "decl"].includes(kind)) {
      const src = { decl: "nazk_decl", fop: "edr_fop", fopx: "yc_fop", orgx: "yc_org" }[kind];
      const r = await soft(sql`SELECT query, title, snippet, url, payload FROM registry_hits WHERE source = ${src} AND ref = ${key} LIMIT 1`.then((x) => x[0]), 4000, null);
      if (r) { out.record = { title: r.title, snippet: r.snippet, url: r.url, ...r.payload }; name = r.payload?.name || r.query || name; }
    }
    if (kind === "case") P.case_info = sql`SELECT nickname, ref, agency, stage, left(summary, 240) summary, count(*)::int posts, min(ts_published)::date::text d0, max(ts_published)::date::text d1
                                           FROM ee_cases WHERE lower(coalesce(nickname, ref)) = ${key} GROUP BY 1, 2, 3, 4, 5 LIMIT 5`;
    name = core(name);
    out.name = name;
    const lname = name.toLowerCase();
    const words = name.split(" ").filter((w) => w.length >= 3);
    // persons are stored surname first; posts often name only the surname, in any case form and spelling
    const isPerson = kind === "person" || kind === "fop" || kind === "fopx";
    // organisations: the most distinctive (longest) word, two when short («НАЕК ЕНЕРГОАТОМ» -> енергоат)
    const longest = [...words].filter((w) => !/^(компанія|компания|підприємство|товариство|національна|державне)$/i.test(w)).sort((a, b) => b.length - a.length);
    const keyWords = (isPerson ? words.slice(0, 1) : longest[0]?.length >= 6 ? longest.slice(0, 1) : longest.slice(0, 2)).map(stem);
    const variants = ukVariants(lname, 4);
    if (keyWords.length) P.posts = corpus({ words: keyWords, limit: 15 });
    if (isPerson && words.length >= 2) P.posts_full = corpus({ words: words.slice(0, 2).map(stem), limit: 5 }).then((r) => ({ found: r.found, more: r.more }));
    P.roles = sql`SELECT role, max(position) position, max(org) org, count(*)::int n, min(ts_published)::date::text d0, max(ts_published)::date::text d1 FROM ee_persons WHERE lower(name_norm) = ANY(${variants}) GROUP BY 1 ORDER BY 4 DESC LIMIT 10`;
    P.cases = sql`SELECT DISTINCT c.nickname, c.ref, c.agency, c.stage, left(c.summary, 200) summary FROM ee_cases c JOIN ee_persons p ON p.message_id = c.message_id
                  WHERE lower(p.name_norm) = ANY(${variants}) LIMIT 10`;
    P.media_cases = sql`SELECT cause_num, role, left(quote, 200) quote, url, confidence FROM media_links WHERE lower(name_norm) = ANY(${variants}) ORDER BY confidence DESC LIMIT 10`;
    P.registry_hits = sql`SELECT source, count(*)::int n, (array_agg(title ORDER BY created_at DESC))[1:5] titles FROM registry_hits WHERE lower(query) = ANY(${variants}) GROUP BY 1`;
    P.edr_roles = words.length >= 2
      ? query(`SELECT edrpou, short_name, stan FROM edr_orgs WHERE (founders || signers || beneficiaries)::text ILIKE $1 LIMIT 15`, ["%" + name + "%"])
      : Promise.resolve([]);
    if ((keyWords[0] || "").length >= 4) P.decisions = query(`SELECT doc_id, cause_num, judgment_form f, adjudication_date::text d, court, doc_url FROM court_decisions WHERE text ~ $1 ${keyWords[1] ? "AND text ~ $2" : ""}
                         ORDER BY adjudication_date DESC NULLS LAST LIMIT 10`, keyWords.slice(0, 2).map(wordRe));
    P.statements = query(`SELECT ts_published::date::text d, actor_side, actor_name, stype, left(quote, 240) quote, status FROM statements
                          WHERE actor_name ~ $1 OR quote ~ $1 ORDER BY ts_published DESC LIMIT 10`, [wordRe(keyWords[0] || lname)]);
    P.web = sql`SELECT title, url, left(snippet, 200) snippet, domain FROM web_mentions WHERE lower(query) = ANY(${variants}) OR title ILIKE ${"%" + (words[0] || name) + "%"} ORDER BY fetched_at DESC LIMIT 10`;
  }
  const keys = Object.keys(P);
  const [g, ...vals] = await Promise.all([gp, ...keys.map((k) => soft(P[k], 7000, { error: "не встигло" }))]);
  const gn = g?.byId.get(id) || null, nb = g ? around(g, id, 2, 80) : null;
  if (gn) { out.label = gn.label; out.node = { ...gn, text: clip(gn.text, 300) }; }
  if (nb) {
    out.neighbourhood = { total: nb.total, shown: nb.shown, by_type: nb.nodes.reduce((a, n) => ((a[n.type] = (a[n.type] || 0) + 1), a), {}),
      top: nb.nodes.slice(1, 25).map((n) => ({ id: n.id, type: n.type, label: clip(n.label, 90) })) };
    // for the page only (chat.js strips it before the model sees the result)
    out._graph = { nodes: nb.nodes.map(({ summary, ...n }) => (summary ? { ...n, summary: clip(summary, 160) } : n)), edges: nb.edges.map((e) => (e.msgs?.length > 5 ? { ...e, msgs: e.msgs.slice(0, 5) } : e)) };
  } else if (!g && graph) out.graph_note = "граф ще будується на цьому інстансі; сусідство не показане";
  keys.forEach((k, j) => { if (vals[j] != null) out[k] = vals[j]; });
  if (out.source?.name && !gn) out.label = out.source.name;
  if (out.posts?.items) out.posts.items = out.posts.items.map(({ ts, ...x }) => x);
  return out;
}

// ---- strikes ground truth: national waves, oblast days (oblast_daily_gt after migration 015, else extracted events), Kharkiv summaries, alerts ----
let rel = null;
async function relations() {
  if (!rel || Date.now() - rel.at > 300e3)
    rel = { at: Date.now(), ...(await query(`SELECT to_regclass('oblast_daily_gt') IS NOT NULL gt, to_regclass('region_daily_strikes') IS NOT NULL rds,
      to_regclass('kharkiv_daily') IS NOT NULL kh, to_regclass('wave_regions') IS NOT NULL wr, to_regclass('alerts') IS NOT NULL al`))[0] };
  return rel;
}
const ymd = (s) => (/^\d{4}-\d{2}-\d{2}$/.test(s || "") ? s : dateOf(s || "") || null);

export async function strikes({ region, date_from, date_to } = {}) {
  const today = kyivToday();
  let d1 = ymd(date_to) || ymd(date_from) || today, d0 = ymd(date_from) || d1;
  if (d0 > d1) [d0, d1] = [d1, d0];
  if ((new Date(d1) - new Date(d0)) / 864e5 > 92) d0 = addDays(d1, -92);
  const o = region ? oblastOf(region) : null;
  if (region && !o) return { error: `не впізнав область «${region}»; дай назву області чи міста або slug (kharkivska, kyiv-city…)` };
  const slug = o?.[0] || "";
  const R = await relations();
  const P = [d0, d1, slug];
  const parts = {
    waves: query(`SELECT * FROM (SELECT DISTINCT ON (id) id, date::text date, kind, launched_missiles, launched_drones, launched_other, intercepted_missiles, intercepted_drones,
                    shot_down_total, hit_locations, debris_locations, is_massive, source, source_url FROM waves WHERE date BETWEEN $1::date AND $2::date AND $3::text = $3::text
                    ORDER BY id, version DESC) w ORDER BY date, id`, P),
    oblast_days: R.gt
      ? query(`SELECT region_id, date::text date, missiles, drones, kab, fpv, attacked, complete, alert_n, alert_min, sources FROM oblast_daily_gt
               WHERE date BETWEEN $1::date AND $2::date AND ($3 = '' OR region_id = $3) AND ($3 <> '' OR attacked)
               ORDER BY date DESC, coalesce(missiles, 0) + coalesce(drones, 0) + coalesce(kab, 0) DESC LIMIT 60`, P)
      : R.rds ? query(`SELECT region region_id, date::text date, missiles, drones, guided_bombs kab, killed, injured, n_reports FROM region_daily_strikes
               WHERE date BETWEEN $1::date AND $2::date AND ($3 = '' OR region = $3) ORDER BY date DESC, missiles + drones + guided_bombs DESC LIMIT 60`, P) : null,
    wave_regions: R.wr && slug ? query(`SELECT w.date::text date, wr.region_id, wr.missiles, wr.drones, wr.source FROM wave_regions wr JOIN waves w ON w.id = wr.wave_id AND w.version = wr.wave_version
               WHERE w.date BETWEEN $1::date AND $2::date AND wr.region_id = $3 ORDER BY 1`, P) : null,
    kharkiv_daily: R.kh && (!slug || slug === "kharkivska") ? query(`SELECT date::text date, missiles, guided_bombs, mlrs, drones, fpv, city_attacked, districts, settlements_n, url
               FROM kharkiv_daily WHERE date BETWEEN $1::date AND $2::date AND $3::text = $3::text ORDER BY date`, P) : null,
    alerts: R.al && slug ? query(`SELECT (ts_start AT TIME ZONE 'Europe/Kyiv')::date::text date, source, count(*)::int n,
               round(sum(extract(epoch FROM least(coalesce(ts_end, ts_start + interval '6 hours'), ts_start + interval '24 hours') - ts_start)) / 60)::int minutes
               FROM alerts WHERE region_id = $3 AND coalesce(level, 'oblast') = 'oblast'
                 AND ts_start >= $1::date::timestamp AT TIME ZONE 'Europe/Kyiv' AND ts_start < ($2::date + 1)::timestamp AT TIME ZONE 'Europe/Kyiv'
               GROUP BY 1, 2 ORDER BY 1, 2`, P) : null,
    events: query(`SELECT e.event_date::text date, e.region, e.missiles, e.drones, e.guided_bombs, e.killed, e.injured, left(e.details, 200) details, e.confidence,
               e.message_id, m.source_id source, m.url FROM extracted_events e JOIN messages m ON m.id = e.message_id
               WHERE e.event_type = 'strike' AND e.event_date BETWEEN $1::date AND $2::date AND ($3 = '' OR e.region = $3 OR (e.region IS NULL AND $3 <> ''))
               ORDER BY e.event_date DESC, (e.region = $3) DESC NULLS LAST, e.missiles + e.drones + e.guided_bombs DESC LIMIT 25`, P),
    // what Ukrainian official channels posted about the oblast on those days (OVA summaries, Air Force)
    ua_posts: slug ? query(`SELECT m.id, m.source_id source, m.ts_published, m.url, left(regexp_replace(m.text, '[[:space:]]+', ' ', 'g'), 300) snippet
               FROM messages m JOIN sources s ON s.id = m.source_id
               WHERE s.country = 'UA' AND s.type = 'official' AND m.ts_published >= $1::date::timestamp AT TIME ZONE 'Europe/Kyiv'
                 AND m.ts_published < ($2::date + 2)::timestamp AT TIME ZONE 'Europe/Kyiv' AND lower(m.text) ~ $4 AND lower(m.text) ~ $5 AND $3::text = $3::text
               ORDER BY m.ts_published DESC LIMIT 12`, [...P, oblastTextRe(o), "(удар|атак|обстріл|ракет|бпла|шахед|каб|влучан|пошкоджен|загинув|поранен)"]) : null,
  };
  const keys = Object.keys(parts).filter((k) => parts[k]);
  const vals = await Promise.all(keys.map((k) => soft(parts[k], 8000, { error: "не встигло" })));
  const out = { region: o ? { id: o[0], name: o[1] } : null, date_from: d0, date_to: d1 };
  keys.forEach((k, j) => { out[k] = vals[j]; });
  if (out.ua_posts?.map) out.ua_posts = out.ua_posts.map((x) => ({ ...x, date: dt(x.ts_published), ts_published: undefined }));
  out.note = "waves — загальнонаціональні нічні/денні хвилі (Повітряні сили, остання версія); oblast_days — " + (R.gt ? "ground truth по областях (зведення ОВА + витягнуті події + тривоги)" : "удари з витягнутих LLM подій (лише позитивні дні; міграція 015 ще не застосована)") +
    "; kharkiv_daily — зведення Синєгубова за добу (обласні цифри); alerts — повітряні тривоги рівня області; events — витягнуті з постів події з посиланнями. Порожній список ≠ «ударів не було», якщо джерело за день не зібране.";
  return out;
}

// ---- Russian-side posts before a date: threat labels when present, rule-based threat words otherwise, compared with the 14 days before ----
const THREAT = "(удар|ракет|обстрел|обстріл|уничтож|знищ|ядерн|орешник|массирован|масован|возмезд|відплат|ответн|ультиматум|сотр[её]м|герань|гераней|калибр|искандер|кинжал|энергетик|енергетик)";
export async function rhetoricBefore({ date, days = 1, region, include_day = false, limit = 20 } = {}) {
  const d = ymd(date) || kyivToday();
  days = Math.max(1, Math.min(7, +days || 1));
  const o = region ? oblastOf(region) : null;
  const from = addDays(d, -days), to = include_day ? addDays(d, 1) : d;
  const reg = o ? oblastTextRe(o) : "";
  const P = [from, to, reg, THREAT];
  const W = `s.country = 'RU' AND m.ts_published >= $1::date::timestamp AT TIME ZONE 'Europe/Kyiv' AND m.ts_published < $2::date::timestamp AT TIME ZONE 'Europe/Kyiv'`;
  const [posts, bySource, base] = await Promise.all([
    query(`SELECT m.id, m.source_id source, s.name src, s.type src_type, m.ts_published, m.url, m.threat_level, m.event_kind, n.label narrative,
             CASE WHEN $3 = '' THEN false ELSE lower(m.text) ~ $3 END mentions_region, lower(m.text) ~ $4 threat_words,
             left(regexp_replace(m.text, '[[:space:]]+', ' ', 'g'), 320) snippet
           FROM messages m JOIN sources s ON s.id = m.source_id LEFT JOIN narratives n ON n.id = m.narrative_id
           WHERE ${W} ORDER BY mentions_region DESC, coalesce(m.threat_level, -1) DESC, threat_words DESC, m.ts_published DESC LIMIT ${Math.min(40, limit)}`, P),
    query(`SELECT m.source_id source, s.name src, s.type src_type, count(*)::int posts, count(m.threat_level)::int labeled,
             count(*) FILTER (WHERE m.threat_level >= 2)::int threat_l2, count(*) FILTER (WHERE lower(m.text) ~ $4)::int threat_words,
             count(*) FILTER (WHERE $3 <> '' AND lower(m.text) ~ $3)::int mentions_region
           FROM messages m JOIN sources s ON s.id = m.source_id WHERE ${W} GROUP BY 1, 2, 3 ORDER BY posts DESC LIMIT 25`, P),
    query(`SELECT round(count(*) / 14.0, 1)::float posts_per_day, round(count(*) FILTER (WHERE lower(m.text) ~ $4) / 14.0, 1)::float threat_words_per_day,
             round(count(*) FILTER (WHERE $3 <> '' AND lower(m.text) ~ $3) / 14.0, 1)::float mentions_region_per_day
           FROM messages m JOIN sources s ON s.id = m.source_id
           WHERE s.country = 'RU' AND m.ts_published >= ($1::date - 14)::timestamp AT TIME ZONE 'Europe/Kyiv' AND m.ts_published < $1::date::timestamp AT TIME ZONE 'Europe/Kyiv'
             AND $2::text = $2::text`, P),
  ]);
  const tot = bySource.reduce((a, r) => ({ posts: a.posts + r.posts, threat_words: a.threat_words + r.threat_words, mentions_region: a.mentions_region + r.mentions_region, threat_l2: a.threat_l2 + r.threat_l2 }), { posts: 0, threat_words: 0, mentions_region: 0, threat_l2: 0 });
  return { window: { from, to_exclusive: to, days, tz: "Europe/Kyiv" }, region: o ? { id: o[0], name: o[1] } : null,
    totals: { ...tot, per_day: { posts: +(tot.posts / days).toFixed(1), threat_words: +(tot.threat_words / days).toFixed(1), mentions_region: +(tot.mentions_region / days).toFixed(1) } },
    baseline_14d: base[0], by_source: bySource,
    posts: posts.map(({ ts_published, ...x }) => ({ ...x, date: dt(ts_published), time: new Date(ts_published).toISOString().slice(11, 16) + "Z" })),
    note: "Канали з sources.country='RU'. threat_level (0–3) — LLM-розмітка, є не для всіх постів; threat_words — простий словник погроз. Порівнюй per_day з baseline_14d. Збіг у часі не доводить звʼязку з ударом." };
}
