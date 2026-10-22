import { sql, query } from "./_db.js";
import { norm, queryWords, wordRe, ukVariants } from "./_text.js";

// Text search for /api/search and the on-the-fly graph (api/node.js?expand=1): posts that mention a query, court decision texts,
// judges, web pages already found for it. No text index on messages (Neon free tier: a trigram index would roughly double the
// database), so a post search is one sequential pass with a case-sensitive LIKE prefilter (the cheap part, ~1 s over ~110k posts)
// and the exact word regex of _text.js on the rows that pass it, under a statement timeout. A trigram GIN index on messages.text
// (not created: storage) would serve the same LIKE without code changes.

// a query capped by statement_timeout (the HTTP driver runs both statements in one transaction); plain query without transactions
const stmt = (t, p) => (typeof sql.query === "function" ? sql.query(t, p) : sql(t, p));
export async function capped(text, params, ms = 2500) {
  if (typeof sql.transaction !== "function") return query(text, params);
  const r = await sql.transaction([stmt(`SET LOCAL statement_timeout = ${Math.round(ms)}`, []), stmt(text, params)], { readOnly: true });
  return r[1];
}

const LEGAL = /^(тов|тзов|пат|прат|ат|тдв|дп|кп|кнп|пп|фг|сфг|го|бф|бо|ооо|оао|зао|ао|фоп|спд|компанія|компания|товариство|підприємство)$/i;
// letters whose spelling varies between Russian and Ukrainian or inside one word's inflection: a LIKE fragment avoids them
const SOFT = /[иіыїеєэёгґьъ'’ʼ`]/i;

// query -> words to require (stems, legal forms dropped), longest first
export function textWords(q) {
  return [...new Set(queryWords(q).map((w) => w.replace(/^[«"“]+|[»"”]+$/g, "")).filter((w) => w && !LEGAL.test(w)))]
    .sort((a, b) => b.length - a.length).slice(0, 3);
}

// LIKE fragments for one word: its longest run of letters spelled the same in Russian and Ukrainian, in lower, Capitalised
// (when the run starts the word) and UPPER case. Short runs fall back to the spelling variants' first letters.
export function likeFragments(w) {
  w = w.toLowerCase();
  if (/^\d/.test(w)) return ["%" + w.replace(/[\\%_]/g, "\\$&") + "%"];
  let best = "", at = 0;
  for (let i = 0; i < w.length; ) {
    if (SOFT.test(w[i])) { i++; continue; }
    let j = i;
    while (j < w.length && !SOFT.test(w[j])) j++;
    if (j - i > best.length) { best = w.slice(i, j); at = i; }
    i = j;
  }
  // at most 6 letters: Russian drops the soft sign Ukrainian writes after a consonant (Коломойский / Коломойський)
  best = best.slice(0, 6);
  const esc = (s) => s.replace(/[\\%_]/g, "\\$&");
  const cases = (s, start) => [...new Set([s, s.toUpperCase(), ...(start ? [s[0].toUpperCase() + s.slice(1)] : [])])].map((x) => "%" + esc(x) + "%");
  if (best.length >= 3) return cases(best, at === 0);
  // e.g. «міндіч»: every spelling's first 4 letters, in every case form (still a handful of patterns)
  const alts = new Set([...ukVariants(w, 4), w.replace(/і/g, "и"), w.replace(/[іи]/g, "ы")].map((v) => v.slice(0, Math.min(5, v.length))));
  return [...new Set([...alts].flatMap((v) => cases(v, true)))].slice(0, 12);
}

const dt = (d) => { if (!d) return null; const x = d instanceof Date ? d : new Date(d); return isNaN(x) ? String(d).slice(0, 10) : x.toISOString().slice(0, 10); };

// posts that mention q: total (capped), how many contain every word, the newest `limit` (all-word matches first)
export async function postMentions(q, { limit = 5, ms = 2500, cap = 1000 } = {}) {
  const ws = textWords(q);
  if (!ws.length || (ws[0].length < 4 && !/^\d/.test(ws[0]))) return { words: ws, total: 0, all: 0, items: [], skipped: "запит закороткий для пошуку в текстах" };
  const P = [likeFragments(ws[0]), wordRe(ws[0])];
  const rest = ws.slice(1).map((w) => (P.push(wordRe(w)), `m.text ~ $${P.length}`));
  const allExpr = rest.length ? rest.join(" AND ") : "true";
  const rows = await capped(`SELECT m.id, m.source_id, s.name src, s.country, m.ts_published, m.url, (${allExpr}) AS all_words,
      left(regexp_replace(coalesce(substring(m.text from '.{0,100}' || $2 || '.{0,180}'), m.text), '[[:space:]]+', ' ', 'g'), 300) snippet,
      count(*) OVER () total, count(*) FILTER (WHERE ${allExpr}) OVER () total_all
    FROM (SELECT * FROM messages WHERE text LIKE ANY($1::text[]) AND text ~ $2 LIMIT ${cap}) m LEFT JOIN sources s ON s.id = m.source_id
    ORDER BY all_words DESC, m.ts_published DESC LIMIT ${limit}`, P, ms);
  const total = rows.length ? +rows[0].total : 0;
  return { words: ws, total, all: rows.length ? +rows[0].total_all : 0, capped: total >= cap,
    items: rows.map((r) => ({ id: "post:" + r.id, msg: r.id, source: r.source_id, src: r.src || r.source_id, country: r.country, date: dt(r.ts_published), url: r.url, all_words: r.all_words, snippet: r.snippet })) };
}

// the same posts for the topic node: more of them, plus their sources, for co-mention edges
export async function topicPosts(q, { limit = 30, ms = 3000 } = {}) {
  return postMentions(q, { limit, ms });
}

// court decision texts that contain the words (trigram index court_decisions_text_trgm serves the ILIKE)
export async function decisionTexts(q, { limit = 5, ms = 2500 } = {}) {
  const ws = textWords(q);
  if (!ws.length || ws[0].length < 4) return { total: 0, items: [] };
  const frag = likeFragments(ws[0]).filter((f) => f.replace(/[%\\]/g, "").length >= 3).map((f) => f.toLowerCase());
  const P = [[...new Set(frag)], ...ws.map(wordRe)];
  const cond = ws.map((_, i) => `text ~ $${i + 2}`).join(" AND ");
  const rows = await capped(`SELECT doc_id, cause_num, judgment_form f, adjudication_date::text d, court, judge,
      left(regexp_replace(coalesce(substring(text from '.{0,100}' || $2 || '.{0,160}'), ''), '[[:space:]]+', ' ', 'g'), 260) snippet, count(*) OVER () total
    FROM court_decisions WHERE text IS NOT NULL AND text ILIKE ANY($1::text[]) AND ${cond} ORDER BY adjudication_date DESC NULLS LAST LIMIT ${limit}`, P, ms);
  return { total: rows.length ? +rows[0].total : 0, items: rows.map(({ total, ...r }) => r) };
}

// judges: the distinct names are few (~3k); cached per warm instance instead of scanning court_decisions per query
let judges = null, judgesAt = 0, judgesLoad = null;
async function judgeList() {
  if (judges && Date.now() - judgesAt < 3600e3) return judges;
  judgesLoad ??= query(`SELECT judge, count(*)::int n, max(court) court FROM court_decisions WHERE judge IS NOT NULL AND judge <> '' GROUP BY 1`)
    .then((r) => { judges = r; judgesAt = Date.now(); return r; }).finally(() => { judgesLoad = null; });
  return judgesLoad;
}
export async function findJudges(q, limit = 6) {
  const ws = textWords(q).filter((w) => !/^\d/.test(w));
  if (!ws.length || ws[0].length < 3) return [];
  const res = ws.map((w) => new RegExp(wordRe(w), "u"));
  const k = norm(q).toLowerCase();
  return (await judgeList()).filter((j) => res.every((r) => r.test(j.judge))).sort((a, b) => b.n - a.n).slice(0, limit)
    .map((j) => ({ id: "person:" + norm(j.judge).toLowerCase(), type: "person", label: norm(j.judge), sub: `суддя · ${j.n} рішень${j.court ? " · " + j.court : ""}`, n: j.n,
      tier: norm(j.judge).toLowerCase() === k ? 0 : 1, judge: true }));
}

// web pages already found for this query by src/web_mentions.py (small table)
export async function webFound(q, limit = 5) {
  const v = ukVariants(q, 4);
  const rows = await sql`SELECT url, title, domain, left(snippet, 200) snippet, fetched_at FROM web_mentions WHERE lower(query) = ANY(${v}) ORDER BY fetched_at DESC LIMIT ${limit + 1}`;
  return { more: rows.length > limit, items: rows.slice(0, limit).map((r) => ({ id: "web:" + r.url, type: "web", label: r.title || r.domain || r.url, sub: r.domain, url: r.url, snippet: r.snippet, date: dt(r.fetched_at) })) };
}
