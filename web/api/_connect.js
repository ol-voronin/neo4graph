import { query } from "./_db.js";
import { getGraph } from "./_graph.js";
import { resolve, CAUSE_ANY, withTimeout } from "./_intent.js";
import { wordRe, stem } from "./_text.js";

// How is A connected to B (a person / company to a case, two people …)? Not a dossier of either: the chains between them,
// each step with its evidence, so the answer can explain the relationship instead of listing rulings.
//   1. graph paths: shortest chains A → … → B (≤ 4 hops) through the entity graph, skipping hub nodes (channels, big posts)
//   2. for a case: where the person is named in the case's decision texts, the sentence and the role it gives
//      (the register depersonalises private persons as ОСОБА_n, officials / lawyers / judges / companies stay named)
//   3. via companies: companies where the person is founder / signer / beneficiary (ЄДР) or declares rights (NAZK) that are
//      named in the case's decisions or posts — an indirect, explicit link
//   4. posts and press that name both (the case number or nickname and the person), with the role the post gives
// Every item says how direct it is; nothing is inferred beyond the evidence.
const soft = (p, ms, d) => withTimeout(Promise.resolve(p), ms).catch(() => d);
const HUB = 250;  // nodes with more links than this (channels, aggregate posts) are not walked through
const KEEP = new Set(["person", "org", "registry", "case", "decision", "declaration", "asset", "wanted", "place", "article", "post", "web"]);

async function side(x) {
  x = String(x || "").trim();
  if (/^[a-z]+:/.test(x)) return { q: x, ids: [x], label: x.slice(x.indexOf(":") + 1), kind: x.slice(0, x.indexOf(":")) };
  const cause = x.match(CAUSE_ANY)?.[0];
  if (cause) return { q: x, ids: ["case:" + cause.toLowerCase()], label: cause, kind: "case", cause };
  const r = await soft(resolve(x), 6000, { candidates: [] });
  const cands = (r.candidates || []).filter((c) => ["person", "org", "registry", "case", "fop"].includes(c.type)).slice(0, 5);
  return { q: x, ids: cands.map((c) => c.id), label: cands[0]?.label || x, kind: cands[0]?.type || "person", candidates: cands.map((c) => ({ id: c.id, label: c.label, sub: c.sub })) };
}

// BFS from every source id at once, parents kept; paths to any target id, shortest first, at most `max`
export function paths(g, from, to, maxDepth = 4, max = 6) {
  const targets = new Set(to.filter((t) => g.byId.has(t)));
  const starts = from.filter((s) => g.byId.has(s));
  if (!targets.size || !starts.length) return [];
  const parent = new Map(starts.map((s) => [s, null]));
  let front = starts, found = [];
  for (let d = 0; d < maxDepth && front.length && found.length < max; d++) {
    const next = [];
    for (const u of front) {
      if (d > 0 && (g.byId.get(u)?.deg || 0) > HUB) continue;
      for (const e of g.adj.get(u) || []) {
        const v = e.source === u ? e.target : e.source;
        if (parent.has(v) || !KEEP.has(g.byId.get(v)?.type)) continue;
        parent.set(v, { u, e });
        if (targets.has(v)) found.push(v); else next.push(v);
      }
    }
    front = next.slice(0, 20000);
  }
  return found.slice(0, max).map((t) => {
    const steps = [];
    for (let v = t; parent.get(v); v = parent.get(v).u) {
      const { u, e } = parent.get(v);
      const A = g.byId.get(u), B = g.byId.get(v);
      steps.unshift({ from: { id: u, type: A.type, label: A.label }, rel: e.rel, to: { id: v, type: B.type, label: B.label },
        detail: e.detail ? String(e.detail).slice(0, 240) : undefined, date: e.date, src: e.src, msgs: (e.msgs || []).slice(0, 2),
        // a step that rests on the name alone (_identity.js): «можливо той самий»
        ...(e.probable ? { probable: true, note: "можливо той самий (єдиний збіг за ПІБ)" } : {}) });
    }
    return { length: steps.length, steps };
  });
}

// sentences of `text` that carry the word (any case form); the role words around it are left to the model to read
function sentences(text, re, max = 4) {
  const out = [];
  for (const s of String(text || "").replace(/\s+/g, " ").split(/(?<=[.;!?])\s+(?=[А-ЯІЇЄҐA-Z0-9])/)) {
    if (re.test(s)) out.push(s.trim().slice(0, 420));
    if (out.length >= max) break;
  }
  return out;
}

// a company's own name: the quoted part if any («Платінум Еко»), else the name without legal form and generic words
const GENERIC = /^(пожеж|держав|україн|област|управлін|товарист|підприєм|банк|служб|публічн|акціонер|національн|компан|головн|район|міськ|приватн|відповідальн|обмежен|спільн|фермер|благодійн|організац|фонд|центр|загін|рятувальн|страхов|ощадн|житлов|ремонтн|експлуатацій|орендн|адвокат|бюро|об.єднан|спілк|асоціац|комунальн)/i;
function ownName(org) {
  const q = String(org).match(/[«"“„]([^«»"“”„]{3,})[»"”]/g);
  const base = q ? q.map((x) => x.slice(1, -1)).join(" ") : String(org);
  return base.split(/[\s,.«»"“”„]+/).filter((w) => w.length >= 4 && !GENERIC.test(w) && !LEGAL.test(w)).slice(0, 3);
}

// a side as a text condition on `text`: a case by its number, a person by surname AND first name stems, an organisation by
// its most distinctive word; params numbered from `from`
const LEGAL = /^(тов|пат|прат|ат|дп|кп|пп|фоп|товариство|компанія|корпорація|державне|підприємство|обмеженою|відповідальністю|акціонерне|приватне)$/i;
function textCond(S, from) {
  if (S.kind === "case") { const c = S.cause || S.label; return { sql: `text LIKE $${from}`, params: ["%" + c + "%"], re: new RegExp(c.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")) }; }
  const name = String(S.candidates?.[0]?.label || S.label || S.q || "").replace(/\s*[,(].*$/, "").replace(/[«»"“”„]/g, " ");
  const words = name.split(/\s+/).filter((w) => w.length >= 3 && !LEGAL.test(w));
  if (!words.length) return { sql: null, params: [] };
  const pick = S.kind === "person" || S.kind === "fop" ? words.slice(0, 2) : [words.sort((x, y) => y.length - x.length)[0]];
  const pats = pick.map((w) => wordRe(stem(w)));
  return { sql: pats.map((_, i) => `text ~* $${from + i}`).join(" AND "), params: pats, re: new RegExp(pats[0], "i") };
}

// people, companies, cases and events within two steps of both sides (not the sides themselves), closest first
const BRIDGE = new Set(["person", "org", "registry", "case", "decision", "declaration", "asset", "wanted", "post"]);
function bridges(g, from, to) {
  const reach = (ids) => {
    const dist = new Map(ids.filter((x) => g.byId.has(x)).map((x) => [x, 0]));
    let front = [...dist.keys()];
    for (let d = 1; d <= 2; d++) {
      const next = [];
      for (const u of front) {
        if (d > 1 && (g.byId.get(u)?.deg || 0) > HUB) continue;
        for (const e of g.adj.get(u) || []) { const v = e.source === u ? e.target : e.source; if (!dist.has(v) && BRIDGE.has(g.byId.get(v)?.type)) { dist.set(v, d); next.push(v); } }
      }
      front = next;
    }
    return dist;
  };
  const da = reach(from), db = reach(to), ends = new Set([...from, ...to]);
  return [...da.keys()].filter((id) => db.has(id) && !ends.has(id))
    .map((id) => { const n = g.byId.get(id); return { id, type: n.type, label: String(n.label).slice(0, 120), hops: [da.get(id), db.get(id)], date: n.date, src: n.src }; })
    .sort((x, y) => x.hops[0] + x.hops[1] - (y.hops[0] + y.hops[1]) || (x.type === "post") - (y.type === "post")).slice(0, 15);
}

export async function connect({ a, b }) {
  const [A, B] = await Promise.all([side(a), side(b)]);
  const out = { a: A, b: B };
  // the case side (if any) and the subject side
  const [C, P] = B.kind === "case" ? [B, A] : A.kind === "case" ? [A, B] : [null, null];
  const g = await soft(getGraph(), 9000, null);
  out.paths = g ? paths(g, A.ids, B.ids) : [];
  if (!g) out.graph_note = "граф ще будується; ланцюжки в графі не перевірені";

  if (C && P) {
    const cause = C.cause || C.label;
    const name = (P.candidates?.[0]?.label || P.label || P.q).replace(/\s*[,(].*$/, "");
    const words = name.split(/\s+/).filter((w) => w.length >= 3);
    const sur = words[0] || name;
    const rx = new RegExp(wordRe(stem(sur)), "i");
    const [dec, posts, media, roles, companies] = await Promise.all([
      soft(query(`SELECT doc_id, judgment_form f, adjudication_date::text d, court, judge, doc_url, text IS NOT NULL has_text,
                    CASE WHEN text ~* $2 THEN text END text FROM court_decisions WHERE lower(cause_num) = lower($1) ORDER BY adjudication_date`, [cause, wordRe(stem(sur))]), 6000, []),
      soft(query(`SELECT id, source_id, ts_published::date::text d, url, left(regexp_replace(text, '[[:space:]]+', ' ', 'g'), 600) t FROM messages
                  WHERE text LIKE $1 AND text ~* $2 ORDER BY ts_published DESC LIMIT 8`, ["%" + cause + "%", wordRe(stem(sur))]), 6000, []),
      soft(query(`SELECT name_norm, role, position, left(quote, 300) quote, url, confidence FROM media_links WHERE lower(cause_num) = lower($1)
                  AND name_norm ~* $2 ORDER BY confidence DESC LIMIT 10`, [cause, wordRe(stem(sur))]), 4000, []),
      // a post about the case (ee_cases with this number) naming the person with a role
      soft(query(`SELECT p.name_norm, p.role, p.position, p.org, m.source_id, m.ts_published::date::text d, m.url FROM ee_cases c
                  JOIN ee_persons p ON p.message_id = c.message_id JOIN messages m ON m.id = c.message_id
                  WHERE (c.ref ILIKE $1 OR m.text LIKE $1) AND p.name_norm ~* $2 LIMIT 10`, ["%" + cause + "%", wordRe(stem(sur))]), 4000, []),
      // the person's companies (ЄДР roles; declared corporate rights) — checked against the case texts below
      soft(query(`SELECT DISTINCT edrpou, coalesce(short_name, name) org, 'ЄДР' src FROM edr_orgs
                  WHERE (founders || signers || beneficiaries)::text ~* $1 ${words.length >= 2 ? "AND (founders || signers || beneficiaries)::text ~* $2" : "AND $2::text IS NOT NULL"} LIMIT 30`,
        [wordRe(stem(sur)), wordRe(stem(words[1] || sur))]), 5000, []),
    ]);
    const texts = dec.filter((d) => d.text);
    out.case = { cause, decisions: dec.length, with_text: dec.filter((d) => d.has_text).length,
      note: dec.length ? undefined : "рішень цієї справи в базі ще немає — get_case поставить її в чергу" };
    out.case_text_mentions = texts.flatMap((d) => sentences(d.text, rx).map((s) => ({ doc_id: d.doc_id, form: d.f, date: d.d, court: d.court, url: d.doc_url, sentence: s }))).slice(0, 12);
    if (dec.length && !texts.length) out.case_text_note = `У ${out.case.with_text} завантажених текстах справи прізвище «${sur}» не трапляється (приватних осіб реєстр знеособлює як ОСОБА_n); тексти решти рішень ще не завантажені.`;
    // companies of the person that the case's texts name (by name or ЄДРПОУ)
    const allText = dec.map((d) => d.text || "").join("\n");
    const decl = await soft(query(`SELECT DISTINCT rel_name org, rel_code edrpou, 'декларація НАЗК' src FROM decl_items WHERE declarant ~* $1 AND rel_kind = 'org' AND rel_name IS NOT NULL LIMIT 40`,
      [wordRe(stem(sur))]), 4000, []);
    // a company counts only by its ЄДРПОУ or by its own name (the quoted part, without generic words) found in the texts
    const seenC = new Set();
    out.via_companies = [...companies, ...decl].filter((c) => {
      if (!c.org || seenC.has(c.edrpou || c.org)) return false;
      seenC.add(c.edrpou || c.org);
      if (c.edrpou && allText.includes(c.edrpou)) return (c.how = "ЄДРПОУ в тексті рішення");
      // the person's own name inside a company name («Адвокатське бюро Дмитра Соляник») proves nothing: drop those words
      const own = ownName(c.org).filter((w) => !words.some((pw) => stem(pw.toLowerCase()) === stem(w.toLowerCase())));
      if (own.length && own.every((w) => new RegExp(wordRe(stem(w)), "i").test(allText))) return (c.how = "назва в тексті рішення");
      return false;
    }).slice(0, 10).map((c) => ({ ...c, link: `компанія особи (${c.src}) згадується в рішеннях справи: ${c.how}`,
      caution: words.length < 2 ? "особу знайдено лише за прізвищем — компанія може належати однофамільцю" : undefined }));
    out.person_companies_checked = companies.length + decl.length;
    out.co_posts = posts.map((p) => ({ ...p, t: sentences(p.t, rx, 2).join(" … ") || p.t.slice(0, 300) }));
    out.media = media;
    out.post_roles = roles;
  }
  // ---- indirect, for any pair: intermediaries (people, companies, cases, events within two steps of both in the graph),
  // posts and extracted events that name both, court decisions whose text names both ----
  if (g) out.bridges = bridges(g, A.ids, B.ids);
  const fa = textCond(A, 1), fb = textCond(B, fa.params.length + 1);
  if (fa.sql && fb.sql) {
    const P = [...fa.params, ...fb.params];
    const [both, events, decs] = await Promise.all([
      out.co_posts ? out.co_posts : soft(query(`SELECT id, source_id, ts_published::date::text d, url, left(regexp_replace(text, '[[:space:]]+', ' ', 'g'), 400) t FROM messages
                  WHERE ${fa.sql} AND ${fb.sql} ORDER BY ts_published DESC LIMIT 8`, P), 6000, []),
      soft(query(`SELECT e.event_type, e.event_date::text d, e.region, e.actor, left(e.details, 200) details, m.source_id, m.url FROM extracted_events e JOIN messages m ON m.id = e.message_id
                  WHERE ${fa.sql.replaceAll("text", "m.text")} AND ${fb.sql.replaceAll("text", "m.text")} ORDER BY e.event_date DESC NULLS LAST LIMIT 10`, P), 6000, []),
      C ? [] : soft(query(`SELECT doc_id, cause_num, judgment_form f, adjudication_date::text d, court, doc_url, text FROM court_decisions
                  WHERE text IS NOT NULL AND ${fa.sql} AND ${fb.sql} ORDER BY adjudication_date DESC LIMIT 6`, P), 6000, []),
    ]);
    out.co_posts = both;
    out.events_with_both = events;
    if (decs.length) out.decisions_with_both = decs.map((d) => ({ doc_id: d.doc_id, cause_num: d.cause_num, form: d.f, date: d.d, court: d.court, url: d.doc_url,
      sentences: [...sentences(d.text, fa.re, 2), ...sentences(d.text, fb.re, 2)].slice(0, 4) }));
  }
  const direct = (out.case_text_mentions?.length || 0) + (out.media?.length || 0) + (out.post_roles?.length || 0);
  out.summary = {
    graph_paths: out.paths.length, shortest: out.paths[0]?.length ?? null,
    named_in_case_texts: out.case_text_mentions?.length ?? null, via_companies: out.via_companies?.length ?? null,
    co_posts: out.co_posts?.length ?? null, media: out.media?.length ?? null,
    bridges: out.bridges?.length ?? null, events_with_both: out.events_with_both?.length ?? null, decisions_with_both: out.decisions_with_both?.length ?? null,
    strength: direct ? "пряма (названий у документах чи постах про справу)" : out.via_companies?.length ? "опосередкована (через компанію)"
      : out.decisions_with_both?.length || out.events_with_both?.length || out.co_posts?.length ? "опосередкована (разом у рішеннях, подіях чи постах)"
      : out.paths.length ? "через граф" : out.bridges?.length ? "через спільних посередників" : "не знайдено",
  };
  // the page draws the chains
  const N = new Map(), E = [];
  for (const p of out.paths) for (const s of p.steps) {
    for (const x of [s.from, s.to]) if (!N.has(x.id)) N.set(x.id, { id: x.id, type: x.type, label: x.label });
    E.push({ source: s.from.id, target: s.to.id, rel: s.rel, detail: s.detail, msgs: s.msgs || [], ...(s.probable ? { probable: true } : {}) });
  }
  out._graph = { nodes: [...N.values()], edges: E, focus: B.ids[0] || A.ids[0] };
  return out;
}

// Three or more objects (X, Y, Z…): every pair through connect(), plus the objects that sit next to all of them in the
// graph (a shared company, case, address, post) — the usual answer to «how are X, Y and Z connected».
export async function connectMany(items) {
  items = [...new Set((items || []).map((x) => String(x || "").trim()).filter(Boolean))].slice(0, 4);
  if (items.length < 2) return { error: "потрібно щонайменше два обʼєкти" };
  if (items.length === 2) return connect({ a: items[0], b: items[1] });
  const pairs = [];
  for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) pairs.push([items[i], items[j]]);
  const res = await Promise.all(pairs.map(([a, b]) => soft(connect({ a, b }), 20000, { error: "не встигло", a: { q: a }, b: { q: b } })));
  const out = { items, pairs: res.map(({ _graph, ...r }) => r) };
  // shared neighbours: within 2 hops of every item's first candidate, ranked by how close they are to all of them
  const g = await soft(getGraph(), 9000, null);
  const sides = res.length ? [res[0].a, ...res.slice(0, items.length - 1).map((r) => r.b)] : [];
  if (g) {
    const reach = (ids) => {
      const dist = new Map(ids.filter((x) => g.byId.has(x)).map((x) => [x, 0]));
      let front = [...dist.keys()];
      for (let d = 1; d <= 2; d++) {
        const next = [];
        for (const u of front) {
          if (d > 1 && (g.byId.get(u)?.deg || 0) > HUB) continue;
          for (const e of g.adj.get(u) || []) { const v = e.source === u ? e.target : e.source; if (!dist.has(v) && KEEP.has(g.byId.get(v)?.type)) { dist.set(v, d); next.push(v); } }
        }
        front = next;
      }
      return dist;
    };
    const maps = sides.map((s) => reach(s?.ids || []));
    const common = [...(maps[0]?.keys() || [])].filter((id) => maps.every((m) => m.has(id)) && !sides.some((s) => (s?.ids || []).includes(id)))
      .map((id) => ({ id, type: g.byId.get(id).type, label: g.byId.get(id).label, hops: maps.map((m) => m.get(id)), sum: maps.reduce((a, m) => a + m.get(id), 0) }))
      .sort((a, b) => a.sum - b.sum || (g.byId.get(a.id).deg || 0) - (g.byId.get(b.id).deg || 0)).slice(0, 15);
    out.shared = common;
  }
  out.summary = { pairs: res.map((r) => ({ a: r.a?.label || r.a?.q, b: r.b?.label || r.b?.q, ...(r.summary || { error: r.error }) })), shared: out.shared?.length ?? null };
  const N = new Map(), E = [];
  for (const r of res) for (const n of r._graph?.nodes || []) N.set(n.id, n);
  for (const r of res) E.push(...(r._graph?.edges || []));
  for (const c of out.shared || []) N.set(c.id, { id: c.id, type: c.type, label: c.label });
  out._graph = { nodes: [...N.values()], edges: E, focus: out.shared?.[0]?.id || sides[0]?.ids?.[0] };
  return out;
}
