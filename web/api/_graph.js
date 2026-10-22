import { sql } from "./_db.js";

// Full entity graph built from the database (persons, orgs, cases, assets, ЄДР records, evidence posts), kept in memory per
// warm function instance. /api/graph serves slices of it: a light overview and capped neighbourhoods (see graph.js).
export const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
export const caseKey = (label) => "case:" + norm(label).replace(/[«»"“”„]/g, "").replace(/^(операція|операция|справа)\s+/i, "").toLowerCase();

async function build() {
  const [persons, orgs, cases, assets, edr, msgs] = await Promise.all([
    sql`SELECT message_id m, source_id s, ts_published::date t, name_norm n, name_as_written w, role r, position p, org o FROM ee_persons`,
    sql`SELECT message_id m, source_id s, ts_published::date t, name n, edrpou e, kind k, role r FROM ee_orgs`,
    sql`SELECT message_id m, source_id s, ts_published::date t, ref, nickname nick, agency a, stage st, summary sum FROM ee_cases`,
    sql`SELECT message_id m, source_id s, ts_published::date t, type ty, description d, linked_person lp FROM ee_assets`,
    sql`SELECT edrpou, short_name name, name "full", stan, founders, beneficiaries benef, signers, matched_queries q FROM edr_orgs`,
    sql`SELECT e.message_id m, mm.source_id s, mm.ts_published::date t, mm.url u, left(regexp_replace(mm.text, '[[:space:]]+', ' ', 'g'), 260) x
        FROM entity_extractions e JOIN messages mm ON mm.id = e.message_id WHERE e.is_case_related`,
  ]);
  const nodes = new Map(), edges = new Map(), msgMap = new Map(msgs.map((x) => [x.m, x]));
  const node = (id, type, label, attrs = {}) => { if (!nodes.has(id)) nodes.set(id, { id, type, label, ...attrs }); return id; };
  const edge = (a, b, rel, m, attrs = {}) => {
    if (a === b) return;
    const k = a + "|" + b + "|" + rel;
    const e = edges.get(k) || edges.set(k, { source: a, target: b, rel, msgs: [], ...attrs }).get(k);
    if (m && !e.msgs.includes(m)) e.msgs.push(m);
  };
  const post = (m) => { const x = msgMap.get(m); return node("post:" + m, "post", x ? x.x.slice(0, 70) + "…" : m, { src: x?.s, date: x?.t, url: x?.u, text: x?.x }); };
  const caseByMsg = new Map();
  for (const c of cases) {
    const label = c.nick || c.ref || (c.sum || "").slice(0, 60);
    const id = c.nick || c.ref ? caseKey(c.nick || c.ref) : "case:" + c.m;
    node(id, "case", norm(label).replace(/^(операція|операция)\s+/i, ""), { agency: c.a, stage: c.st, summary: c.sum, ref: c.ref });
    caseByMsg.set(c.m, [...(caseByMsg.get(c.m) || []), id]);
    edge(post(c.m), id, "reports", c.m, { date: c.t, src: c.s });
  }
  for (const p of persons) {
    const id = node("person:" + norm(p.n).toLowerCase(), "person", norm(p.n), { position: p.p });
    const cs = caseByMsg.get(p.m) || [];
    for (const ck of cs) edge(id, ck, p.r, p.m, { date: p.t, src: p.s });
    if (!cs.length) edge(id, post(p.m), p.r, p.m, { date: p.t, src: p.s });
    if (p.o) edge(id, node("org:" + norm(p.o).toLowerCase(), "org", norm(p.o)), "affiliated", p.m, { date: p.t, src: p.s });
  }
  for (const o of orgs) {
    const id = node("org:" + norm(o.n).toLowerCase(), "org", norm(o.n), { kind: o.k });
    if (o.e) nodes.get(id).edrpou = o.e;
    for (const ck of caseByMsg.get(o.m) || []) edge(id, ck, o.r || "involved", o.m, { date: o.t, src: o.s });
  }
  for (const a of assets) {
    const id = node("asset:" + (a.d || "").toLowerCase() + "|" + (a.lp || ""), "asset", a.d, { kind: a.ty });
    if (a.lp) edge(node("person:" + norm(a.lp).toLowerCase(), "person", norm(a.lp)), id, "owns_or_linked", a.m, { date: a.t, src: a.s });
    for (const ck of caseByMsg.get(a.m) || []) edge(id, ck, "seized_or_named", a.m, { date: a.t, src: a.s });
  }
  function objFor(qq) {
    const k = qq.toLowerCase().replace(/\s+/g, " ").trim();
    for (const c of ["person:" + k, "org:" + k]) if (nodes.has(c)) return c;
    return node("person:" + k, /^(тов|пат|прат|ат|дп|кп)\b/i.test(k) ? "org" : "person", qq.replace(/\b\w/gu, (c) => c.toUpperCase()));
  }
  for (const e of edr) {
    const rk = node("registry:" + e.edrpou, "registry", `ЄДР ${e.edrpou} · ${e.name}`, { stan: e.stan, full: e.full, edrpou: e.edrpou });
    for (const q of e.q || []) { const ok = node("org:" + norm(q).toLowerCase(), "org", norm(q)); nodes.get(ok).edrpou = e.edrpou; edge(ok, rk, "registry_record", null); }
    // Founders are kept under their FULL name (surname, name, patronymic): two people who share surname and name are
    // different nodes. A person query ("Кравченко Дмитро") is linked to each full-name match with a "namesake" edge,
    // so the user sees the candidates and picks by patronymic instead of the graph merging strangers into one person.
    const pq = (e.q || []).filter((x) => /^person:/.test(x)).map((x) => x.slice(7).toLowerCase());
    const big = (e.founders || []).length > 60;  // cooperatives with thousands of members: list only query matches
    nodes.get(rk).n_founders = (e.founders || []).length;
    const people = (list, rel) => { for (const f of list || []) {
      const nm = String(f).split(";")[0].trim(); if (!nm) continue;
      const tail = String(f).split(";")[1] || "";
      // an 8-digit ЄДРПОУ in the tail = a founding company; a 10-digit ІПН there belongs to a person
      if (/(?<!\d)\d{8}(?!\d)/.test(tail)) { if (!big) edge(node("org:" + nm.toLowerCase(), "org", nm), rk, "founder", null); continue; }
      const low = nm.toLowerCase().replace(/\s+/g, " ");
      const hit = pq.find((q) => low.startsWith(q));
      if (big && !hit) continue;
      const title = low.replace(/[^\s'’-]+/g, (w) => w[0].toUpperCase() + w.slice(1));
      const pid = node("person:" + low, "person", title);
      edge(pid, rk, rel, null, { detail: String(f) });
      if (hit && low !== hit) edge(objFor(hit), pid, "namesake", null, { detail: "збіг прізвища та імені у ЄДР; це той самий чи інший — визначає по батькові" });
    } };
    people(e.founders, "founder"); people(e.benef, "beneficiary"); people(e.signers, "signer");
  }
  // query-driven findings: corpus hits and web mentions attach to the object that was searched
  const [hits, web] = await Promise.all([
    sql`SELECT h.query, h.message_id m, h.where_found w, mm.source_id s, mm.ts_published::date t, mm.url u, left(regexp_replace(mm.text,'[[:space:]]+',' ','g'),260) x
        FROM query_hits h JOIN messages mm ON mm.id = h.message_id ORDER BY mm.ts_published DESC LIMIT 2000`.catch(() => []),
    sql`SELECT query, url, title, snippet, domain, engine FROM web_mentions ORDER BY fetched_at DESC LIMIT 1000`.catch(() => []),
  ]);
  for (const h of hits) {
    if (!msgMap.has(h.m)) msgMap.set(h.m, { m: h.m, s: h.s, t: h.t, u: h.u, x: h.x });
    edge(objFor(h.query), post(h.m), "mentioned_in", h.m, { date: h.t, src: h.s });
  }
  for (const w of web) {
    if (/site:/.test(w.query)) continue;
    const wid = node("web:" + w.url, "web", (w.title || w.domain || w.url).slice(0, 80), { url: w.url, text: w.snippet, src: w.domain, engine: w.engine });
    edge(objFor(w.query), wid, "web_mention", null, { src: w.domain });
  }
  for (const w of web) {
    if (!/site:reyestr/.test(w.query)) continue;
    const base = w.query.replace(/\s*site:\S+/, "").replace(/"/g, "");
    const wid = node("web:" + w.url, "web", (w.title || w.url).slice(0, 80), { url: w.url, text: w.snippet, src: "reyestr.court.gov.ua", engine: w.engine });
    edge(objFor(base), wid, "court_register_mention", null, { src: "reyestr.court.gov.ua" });
  }
  // media claims: who the press names in connection with a case (quote + URL as evidence); not a registry fact
  const media = await sql`SELECT cause_num, name_norm, role, position, quote, url, confidence FROM media_links ORDER BY confidence DESC LIMIT 1000`.catch(() => []);
  for (const ml of media) {
    const cid = node("case:" + ml.cause_num.toLowerCase(), "case", "Справа " + ml.cause_num, { ref: ml.cause_num });
    const pid = node("person:" + norm(ml.name_norm).toLowerCase(), "person", norm(ml.name_norm), { position: ml.position });
    edge(pid, cid, "named_by_media", null, { detail: `за даними ЗМІ (${Math.round((ml.confidence || 0) * 100)}%): ${ml.role} — «${(ml.quote || "").slice(0, 160)}» ${ml.url}` });
  }
  // registry hits from the local worker (Ukrainian IP): court register full-text search, NAZK declarations, ЄДР ФОП
  const [rhits, alinks] = await Promise.all([
    sql`SELECT query, qkind, source, ref, title, snippet, url, payload FROM registry_hits ORDER BY created_at DESC LIMIT 3000`.catch(() => []),
    sql`SELECT src_kind, src_name, dst_kind, dst_name, relation, note, created_at::date d FROM analyst_links`.catch(() => []),
  ]);
  for (const h of rhits) {
    const p = h.payload || {};
    const who = objFor(h.query);
    if (h.source === "reyestr") {
      const did = node("decision:" + h.ref, "decision", `${p.judgment_form || "Рішення"} ${p.adjudication_date || ""}`, { doc_id: h.ref, form: p.judgment_form, kind: p.justice_kind,
        court: p.court, judge: p.judge, date: p.adjudication_date, receipt: p.receipt_date, url: h.url, status: "1", text: h.snippet, src: "reyestr.court.gov.ua" });
      if (p.cause_num) edge(did, node("case:" + p.cause_num.toLowerCase(), "case", "Справа " + p.cause_num, { ref: p.cause_num }), "decision_of", null);
      edge(who, did, "court_register_mention", null, { detail: h.snippet, src: "reyestr.court.gov.ua" });
    } else if (h.source === "nazk_decl") {
      const did = node("decl:" + h.ref, "declaration", h.title, { url: h.url, text: h.snippet, position: p.work_post, org: p.work_place, year: p.year, region: p.region, src: "НАЗК" });
      edge(who, did, "declaration", null, { detail: h.snippet, src: "public.nazk.gov.ua" });
    } else if (h.source === "yc_fop") {
      const fid = node("fopx:" + h.ref, "registry", h.title, { stan: p.status, text: h.snippet, kved_main: p.kved_main, kved_other: p.kved_other, registration: p.registration, city: p.city, url: h.url, src: "ЄДР (через YouControl)" });
      edge(who, fid, "fop_record", null, { detail: h.snippet, src: "youcontrol.com.ua" });
    } else if (h.source === "yc_org") {
      const oid = node("orgx:" + h.ref, "registry", h.title, { stan: p.status, text: h.snippet, kved_main: p.kved_main, kved_other: p.kved_other, registration: p.registration,
        capital: p.capital, address: p.address, head: p.head, head_since: p.head_since, vat: p.vat, assets: p.assets, court_docs: p.court_docs, edrpou: p.edrpou, url: h.url, src: "ЄДР (через YouControl)" });
      edge(who, oid, "company_card", null, { detail: h.snippet, src: "youcontrol.com.ua" });
      if (p.head && /^\p{Lu}/u.test(p.head)) {
        const hid = node("person:" + norm(p.head).toLowerCase(), "person", norm(p.head), { position: p.head_role || "керівник" });
        edge(hid, who, "director", null, { detail: `${p.head_role || "керівник"}${p.head_since ? " з " + p.head_since : ""} (ЄДР, через YouControl)`, src: "youcontrol.com.ua" });
      }
    } else if (h.source === "edr_role") {
      const oid = node("registry:" + p.edrpou, "registry", `ЄДР ${p.edrpou} · ${p.short_name || p.org_name || ""}`, { edrpou: p.edrpou, stan: p.stan, full: p.org_name, src: "ЄДР" });
      edge(who, oid, { founder: "founder", beneficiary: "beneficiary", signer: "director" }[p.role] || "related", null, { detail: h.snippet, src: "ЄДР (data lake)" });
    } else if (h.source === "erb") {
      const eid = node("erb:" + h.ref, "registry", h.title, { text: h.snippet, org: p.executor, agency: p.publisher, year: p.birth_year ? null : null, src: "Єдиний реєстр боржників", url: h.url, category: p.category });
      edge(who, eid, "debtor_record", null, { detail: h.snippet, src: "erb.minjust.gov.ua" });
    } else if (h.source === "edr_fop") {
      const fid = node("fop:" + h.ref, "registry", h.title, { stan: p.stan, text: h.snippet, src: "ЄДР (ФОП)" });
      edge(who, fid, "fop_record", null, { detail: h.snippet, src: "ЄДР" });
    }
  }
  // analyst-supplied links (the researcher's own knowledge): kept apart from registry facts and labelled as such
  for (const l of alinks) {
    const a = node(l.src_kind + ":" + norm(l.src_name).toLowerCase(), l.src_kind, norm(l.src_name));
    const b = node(l.dst_kind + ":" + norm(l.dst_name).toLowerCase(), l.dst_kind, norm(l.dst_name));
    edge(a, b, l.relation, null, { detail: `вказано аналітиком ${l.d || ""}${l.note ? ": " + l.note : ""}`, src: "analyst" });
  }
  // public money: spending.gov.ua transactions to companies in the graph (payer state body -> company, totals per pair)
  const pay = await sql`SELECT recipt_edrpou e, payer_edrpou pe, max(payer_name) pn, count(*)::int n, sum(amount)::float total, min(trans_date)::date d0, max(trans_date)::date d1
                        FROM public_payments GROUP BY 1, 2 ORDER BY total DESC LIMIT 2000`.catch(() => []);
  for (const r of pay) {
    const rk = "registry:" + r.e;
    if (!nodes.has(rk)) continue;
    const pid = node("org:" + norm(r.pn || r.pe).toLowerCase(), "org", norm(r.pn || r.pe), { kind: "state_body", edrpou: r.pe });
    edge(pid, rk, "paid_public_money", null, { detail: `${r.n} платежів на ${Math.round(r.total).toLocaleString("uk-UA")} грн, ${r.d0}…${r.d1} (spending.gov.ua)`, src: "spending.gov.ua", date: r.d1 });
  }
  const srcSet = [...new Set(msgs.map((x) => x.s))].sort();
  return { nodes: [...nodes.values()], edges: [...edges.values()], sources: srcSet, generated: new Date().toISOString().slice(0, 10) };
}

// ---- one build per warm instance; the CDN in front caches /api/graph responses for the same 10 minutes ----
let cache = null, pending = null;
export function getGraph(fresh = false) {
  if (cache && Date.now() - cache.at < (fresh ? 20e3 : 600e3)) return Promise.resolve(cache);
  pending ??= build().then((g) => (cache = index(g))).finally(() => { pending = null; });
  return pending;
}
export function index(g) {
  const byId = new Map(g.nodes.map((n) => [n.id, n])), adj = new Map();
  for (const e of g.edges) for (const x of [e.source, e.target]) (adj.get(x) || adj.set(x, []).get(x)).push(e);
  for (const n of g.nodes) n.deg = (adj.get(n.id) || []).length;
  return { ...g, byId, adj, at: Date.now() };
}
const wire = (e) => (e.msgs.length > 5 ? { ...e, msgs: e.msgs.slice(0, 5), nmsgs: e.msgs.length } : e);
const lite = ({ text, summary, ...n }) => (summary ? { ...n, summary: summary.slice(0, 160) } : n);

// overview: the n most connected non-post nodes and the edges between them
export function overview(g, n = 300) {
  const top = g.nodes.filter((x) => x.type !== "post").sort((a, b) => b.deg - a.deg).slice(0, n), ids = new Set(top.map((x) => x.id)), edges = [];
  for (const x of top) for (const e of g.adj.get(x.id) || []) if (e.source === x.id && ids.has(e.target)) edges.push(wire(e));
  return { nodes: top.map(lite), edges: edges.slice(0, 4000), sources: g.sources, generated: g.generated, total: { nodes: g.nodes.length, edges: g.edges.length } };
}

// neighbourhood: BFS to `depth`, ranked by distance then degree, capped at `limit`; edges among the kept nodes, plus the
// evidence posts (ev) their message ids point to, so the side panel can quote them without shipping every post node
function pack(g, ids, extra = []) {
  const set = new Set(ids), edges = [...extra], ev = {};
  for (const x of ids) for (const e of g.adj.get(x) || []) {
    if (e.source !== x || !set.has(e.target)) continue;
    edges.push(wire(e));
    for (const m of e.msgs.slice(0, 3)) { const p = g.byId.get("post:" + m); if (p && !set.has(p.id)) ev[m] = { date: p.date, src: p.src, text: p.text, url: p.url }; }
  }
  return { nodes: ids.map((id) => g.byId.get(id)), edges, ev };
}
const stem = (s) => { const w = norm(s).toLowerCase().split(" ")[0] || ""; return w.slice(0, Math.max(4, w.length - 3)); };
// places and channels are not graph nodes: their neighbourhood is the most connected nodes that mention the place / come from the channel
function virtual(id) {
  const i = id.indexOf(":"), kind = id.slice(0, i), key = id.slice(i + 1);
  if (kind === "channel") return { type: "channel", rel: "published_in", match: (n) => n.src === key };
  if (kind !== "place") return null;
  const s = stem(key);
  if (s.length < 3) return null;
  return { type: "place", rel: "located_in", match: (n) => [n.region, n.city, n.court, n.address, n.type === "court" || n.type === "post" ? n.label : null].some((v) => v && String(v).toLowerCase().includes(s)) };
}
export function around(g, id, depth = 2, limit = 150) {
  if (!g.byId.has(id)) {
    const v = virtual(id);
    if (!v) return null;
    const hits = g.nodes.filter(v.match).sort((a, b) => b.deg - a.deg), ids = hits.slice(0, limit - 1).map((n) => n.id);
    const r = pack(g, ids, ids.map((x) => ({ source: x, target: id, rel: v.rel, msgs: [] })));
    return { center: id, ...r, nodes: [{ id, type: v.type, label: id.slice(id.indexOf(":") + 1), deg: hits.length }, ...r.nodes], total: hits.length + 1, shown: ids.length + 1 };
  }
  const dist = new Map([[id, 0]]);
  let front = [id];
  for (let d = 1; d <= depth && front.length; d++) {
    const next = [];
    for (const u of front) for (const e of g.adj.get(u) || []) { const x = e.source === u ? e.target : e.source; if (!dist.has(x)) { dist.set(x, d); next.push(x); } }
    front = next;
  }
  const ids = [...dist.keys()].sort((a, b) => dist.get(a) - dist.get(b) || g.byId.get(b).deg - g.byId.get(a).deg).slice(0, limit);
  return { center: id, ...pack(g, ids), total: dist.size, shown: ids.length };
}
