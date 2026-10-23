import { gunzipSync } from "node:zlib";
import { sql } from "./_db.js";
import { declGraph } from "./_decl.js";
import { assetGraph, allAssets } from "./_assets.js";

// Full entity graph (persons, orgs, cases, assets, ЄДР records, evidence posts), kept in memory per warm function instance.
// /api/graph serves slices of it: a light overview and capped neighbourhoods (see graph.js).
//
// Source of the graph, cheapest first (Neon free plan: 5 GB egress a month, a full build reads ~16 MB):
//   1. the snapshot on GitHub release "graph-latest" (scripts/graph_snapshot.mjs, .github/workflows/graph.yml):
//      graph_manifest.json -> graph_<stamp>.json.gz; no Neon traffic at all;
//   2. ?fresh (a registry search just finished): the snapshot plus the rows written since it was built (applyDelta, a few KB);
//   3. a full build from Neon, only when the snapshot is missing or its manifest is older than 24 h.
export const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
// ЄДР and extracted names carry placeholders ("НЕМАЄ", "відсутній", "-") that must not become one giant node
// (also phrases: «Невідома особа - керівник», «Причина відсутності: відсутні фізичні особи…» from ЄДР beneficiary fields)
export const PLACEHOLDER = /^(немає|нема|немає даних|відсутн[а-яії]*(\s.*)?|причин[аи] відсутн.*|не встановлено|не визначено|не вказано|невідом(а|ий|ого|ому|ої|і|е|о)(\s.*)?|нет|нету|отсутству[а-яє]*(\s.*)?|не указан[а-я]*|неизвестн(ый|ая|ое|ые|ого|о)(\s.*)?|н\/?а|n\/?a|none|null|unknown|[-–—.х_x0 ]+)[.!]?$/i;
export const isPlaceholder = (s) => !norm(s) || PLACEHOLDER.test(norm(s).replace(/[«»"“”„]/g, "").replace(/^(тов|пп|фоп)\s+/i, ""));
// a person/org node that is a placeholder, or whose label is a raw id («person:кравченко олександр» as an org): never drawn
export const isJunk = (n) => (n.type === "person" || n.type === "org") && (isPlaceholder(n.label) || isPlaceholder(n.id.slice(n.id.indexOf(":") + 1)) || /^(person|org):/.test(n.label));
export const caseKey = (label) => "case:" + norm(label).replace(/[«»"“”„]/g, "").replace(/^(операція|операция|справа)\s+/i, "").toLowerCase();

// ---- rows: everything (since = null) or only rows written after `since` (a delta on top of a snapshot) ----
// The full-build queries are the original ones, unchanged; the delta variants add a timestamp filter. Every step of a
// delta is idempotent (node() keeps the first version, edge() dedups message ids), so overlapping windows are harmless.
async function loadRows(q, since = null) {
  const S = since;
  const [persons, orgs, cases, assets, edr, msgs] = await Promise.all(S ? [
    q`SELECT message_id m, source_id s, ts_published::date t, name_norm n, name_as_written w, role r, position p, org o FROM ee_persons
      WHERE message_id IN (SELECT message_id FROM entity_extractions WHERE created_at > ${S})`,
    q`SELECT message_id m, source_id s, ts_published::date t, name n, edrpou e, kind k, role r FROM ee_orgs
      WHERE message_id IN (SELECT message_id FROM entity_extractions WHERE created_at > ${S})`,
    q`SELECT message_id m, source_id s, ts_published::date t, ref, nickname nick, agency a, stage st, summary sum FROM ee_cases
      WHERE message_id IN (SELECT message_id FROM entity_extractions WHERE created_at > ${S})`,
    q`SELECT message_id m, source_id s, ts_published::date t, type ty, description d, linked_person lp FROM ee_assets
      WHERE message_id IN (SELECT message_id FROM entity_extractions WHERE created_at > ${S})`,
    q`SELECT edrpou, short_name name, name "full", stan, founders, beneficiaries benef, signers, matched_queries q FROM edr_orgs WHERE updated_at > ${S}`,
    q`SELECT e.message_id m, mm.source_id s, mm.ts_published::date t, mm.url u, left(regexp_replace(mm.text, '[[:space:]]+', ' ', 'g'), 260) x
        FROM entity_extractions e JOIN messages mm ON mm.id = e.message_id WHERE e.is_case_related AND e.created_at > ${S}`,
  ] : [
    q`SELECT message_id m, source_id s, ts_published::date t, name_norm n, name_as_written w, role r, position p, org o FROM ee_persons`,
    q`SELECT message_id m, source_id s, ts_published::date t, name n, edrpou e, kind k, role r FROM ee_orgs`,
    q`SELECT message_id m, source_id s, ts_published::date t, ref, nickname nick, agency a, stage st, summary sum FROM ee_cases`,
    q`SELECT message_id m, source_id s, ts_published::date t, type ty, description d, linked_person lp FROM ee_assets`,
    q`SELECT edrpou, short_name name, name "full", stan, founders, beneficiaries benef, signers, matched_queries q FROM edr_orgs`,
    q`SELECT e.message_id m, mm.source_id s, mm.ts_published::date t, mm.url u, left(regexp_replace(mm.text, '[[:space:]]+', ' ', 'g'), 260) x
        FROM entity_extractions e JOIN messages mm ON mm.id = e.message_id WHERE e.is_case_related`,
  ]);
  const [hits, web, media, rhits, alinks, pay] = await Promise.all(S ? [
    q`SELECT h.query, h.message_id m, h.where_found w, mm.source_id s, mm.ts_published::date t, mm.url u, left(regexp_replace(mm.text,'[[:space:]]+',' ','g'),260) x
        FROM query_hits h JOIN messages mm ON mm.id = h.message_id WHERE h.created_at > ${S} ORDER BY mm.ts_published DESC LIMIT 2000`.catch(() => []),
    q`SELECT query, url, title, snippet, domain, engine FROM web_mentions WHERE fetched_at > ${S} ORDER BY fetched_at DESC LIMIT 1000`.catch(() => []),
    q`SELECT cause_num, name_norm, role, position, quote, url, confidence FROM media_links WHERE created_at > ${S} ORDER BY confidence DESC LIMIT 1000`.catch(() => []),
    q`SELECT query, qkind, source, ref, title, snippet, url, payload FROM registry_hits WHERE created_at > ${S} ORDER BY created_at DESC LIMIT 3000`.catch(() => []),
    q`SELECT src_kind, src_name, dst_kind, dst_name, relation, note, created_at::date d FROM analyst_links WHERE created_at > ${S}`.catch(() => []),
    // totals per (recipient, payer) pair for recipients that received new payments: recomputed in full, not summed up
    q`SELECT recipt_edrpou e, payer_edrpou pe, max(payer_name) pn, count(*)::int n, sum(amount)::float total, min(trans_date)::date d0, max(trans_date)::date d1
        FROM public_payments WHERE recipt_edrpou IN (SELECT recipt_edrpou FROM public_payments WHERE fetched_at > ${S}) GROUP BY 1, 2 ORDER BY total DESC LIMIT 2000`.catch(() => []),
  ] : [
    q`SELECT h.query, h.message_id m, h.where_found w, mm.source_id s, mm.ts_published::date t, mm.url u, left(regexp_replace(mm.text,'[[:space:]]+',' ','g'),260) x
        FROM query_hits h JOIN messages mm ON mm.id = h.message_id ORDER BY mm.ts_published DESC LIMIT 2000`.catch(() => []),
    q`SELECT query, url, title, snippet, domain, engine FROM web_mentions ORDER BY fetched_at DESC LIMIT 1000`.catch(() => []),
    q`SELECT cause_num, name_norm, role, position, quote, url, confidence FROM media_links ORDER BY confidence DESC LIMIT 1000`.catch(() => []),
    q`SELECT query, qkind, source, ref, title, snippet, url, payload FROM registry_hits ORDER BY created_at DESC LIMIT 3000`.catch(() => []),
    q`SELECT src_kind, src_name, dst_kind, dst_name, relation, note, created_at::date d FROM analyst_links`.catch(() => []),
    q`SELECT recipt_edrpou e, payer_edrpou pe, max(payer_name) pn, count(*)::int n, sum(amount)::float total, min(trans_date)::date d0, max(trans_date)::date d1
                        FROM public_payments GROUP BY 1, 2 ORDER BY total DESC LIMIT 2000`.catch(() => []),
  ]);
  // police wanted records for people the graph already names (migration 021): full names (ЄДР founders / signers /
  // beneficiaries, extracted persons, searched names) -> m = 'full'; two-word names («Прізвище Імʼя») -> m = 'short', drawn
  // as a namesake candidate. ~1 100 rows on 2026-10-22.
  // full builds only (the snapshot is rebuilt daily, the list changes daily); a delta skips it
  const wanted = S ? [] : await q`WITH nm AS MATERIALIZED (
        SELECT lower(regexp_replace(btrim(split_part(f, ';', 1)), '\\s+', ' ', 'g')) n FROM edr_orgs,
               jsonb_array_elements_text(coalesce(founders, '[]'::jsonb) || coalesce(signers, '[]'::jsonb) || coalesce(beneficiaries, '[]'::jsonb)) f
        UNION SELECT lower(regexp_replace(btrim(name_norm), '\\s+', ' ', 'g')) FROM ee_persons
        UNION SELECT lower(regexp_replace(btrim(name_norm), '\\s+', ' ', 'g')) FROM media_links
        UNION SELECT lower(regexp_replace(btrim(query), '\\s+', ' ', 'g')) FROM registry_hits),
      w AS MATERIALIZED (SELECT * FROM wanted_persons WHERE removed IS NULL),
      x AS (SELECT w.id, 'full' m FROM w JOIN nm ON nm.n = w.name_u
            UNION ALL SELECT w.id, 'short' m FROM w JOIN nm ON nm.n = lower(w.last_name_u || ' ' || w.first_name_u))
      SELECT DISTINCT ON (w.id) w.id, w.name_u, w.last_name_u, w.first_name_u, w.middle_name_u, w.birth_date::text b, w.category, w.article_crim, w.article,
             w.restraint, w.ovd, w.agency, w.lost_date::text d, x.m
      FROM x JOIN w USING (id) ORDER BY w.id, x.m`.catch((e) => { console.warn("wanted rows:", e.message); return []; });
  // contents of NAZK declarations fetched by the registry chain (migration 023): assets, companies, income sources, family
  const decl = await (S ? q`SELECT decl_id, year, declarant, step, kind, label, owner, rel_name, rel_code, rel_kind, value::float value, currency, since, region
                            FROM decl_items WHERE created_at > ${S}`
                        : q`SELECT decl_id, year, declarant, step, kind, label, owner, rel_name, rel_code, rel_kind, value::float value, currency, since, region
                            FROM decl_items`).catch((e) => { console.warn("decl rows:", e.message); return []; });
  // property in court cases (migrations 024–025): proceedings named in decisions, items from arrest rulings, ARMA assets
  const caseAssets = S ? { links: [], items: [], arma: [] } : await allAssets(q);
  return { persons, orgs, cases, assets, edr, msgs, hits, web, media, rhits, alinks, pay, wanted, decl, caseAssets };
}

// ---- assemble rows into a graph; with `base` the rows are merged into an existing graph (its node/edge objects are reused) ----
export function assemble(R, base = null) {
  const nodes = new Map(), edges = new Map(), msgMap = new Map(R.msgs.map((x) => [x.m, x]));
  if (base) {
    for (const n of base.nodes) nodes.set(n.id, n);
    for (const e of base.edges) edges.set(e.source + "|" + e.target + "|" + e.rel, e);
  }
  const node = (id, type, label, attrs = {}) => { if (!nodes.has(id)) nodes.set(id, { id, type, label, ...attrs }); return id; };
  const edge = (a, b, rel, m, attrs = {}) => {
    if (a === b) return null;
    const k = a + "|" + b + "|" + rel;
    const e = edges.get(k) || edges.set(k, { source: a, target: b, rel, msgs: [], ...attrs }).get(k);
    if (m && !e.msgs.includes(m)) e.msgs.push(m);
    return e;
  };
  const post = (m) => { const x = msgMap.get(m); return node("post:" + m, "post", x ? x.x.slice(0, 70) + "…" : m, { src: x?.s, date: x?.t, url: x?.u, text: x?.x }); };
  const caseByMsg = new Map();
  for (const c of R.cases) {
    const label = c.nick || c.ref || (c.sum || "").slice(0, 60);
    const id = c.nick || c.ref ? caseKey(c.nick || c.ref) : "case:" + c.m;
    node(id, "case", norm(label).replace(/^(операція|операция)\s+/i, ""), { agency: c.a, stage: c.st, summary: c.sum, ref: c.ref });
    caseByMsg.set(c.m, [...(caseByMsg.get(c.m) || []), id]);
    edge(post(c.m), id, "reports", c.m, { date: c.t, src: c.s });
  }
  for (const p of R.persons) {
    if (isPlaceholder(p.n)) continue;
    const id = node("person:" + norm(p.n).toLowerCase(), "person", norm(p.n), { position: p.p });
    const cs = caseByMsg.get(p.m) || [];
    for (const ck of cs) edge(id, ck, p.r, p.m, { date: p.t, src: p.s });
    if (!cs.length) edge(id, post(p.m), p.r, p.m, { date: p.t, src: p.s });
    if (p.o && !isPlaceholder(p.o)) edge(id, node("org:" + norm(p.o).toLowerCase(), "org", norm(p.o)), "affiliated", p.m, { date: p.t, src: p.s });
  }
  for (const o of R.orgs) {
    if (isPlaceholder(o.n)) continue;
    const id = node("org:" + norm(o.n).toLowerCase(), "org", norm(o.n), { kind: o.k });
    if (o.e) nodes.get(id).edrpou = o.e;
    for (const ck of caseByMsg.get(o.m) || []) edge(id, ck, o.r || "involved", o.m, { date: o.t, src: o.s });
  }
  for (const a of R.assets) {
    const id = node("asset:" + (a.d || "").toLowerCase() + "|" + (a.lp || ""), "asset", a.d, { kind: a.ty });
    if (a.lp && !isPlaceholder(a.lp)) edge(node("person:" + norm(a.lp).toLowerCase(), "person", norm(a.lp)), id, "owns_or_linked", a.m, { date: a.t, src: a.s });
    for (const ck of caseByMsg.get(a.m) || []) edge(id, ck, "seized_or_named", a.m, { date: a.t, src: a.s });
  }
  function objFor(qq) {
    const k = qq.toLowerCase().replace(/\s+/g, " ").trim();
    for (const c of ["person:" + k, "org:" + k]) if (nodes.has(c)) return c;
    return node("person:" + k, /^(тов|пат|прат|ат|дп|кп)\b/i.test(k) ? "org" : "person", qq.replace(/\b\w/gu, (c) => c.toUpperCase()));
  }
  for (const e of R.edr) {
    const rk = node("registry:" + e.edrpou, "registry", `ЄДР ${e.edrpou} · ${e.name}`, { stan: e.stan, full: e.full, edrpou: e.edrpou });
    // matched_queries carry person queries as «person:<name>» (handled by the founders pass below), not org names
    for (const q of e.q || []) { if (/^person:/.test(q) || isPlaceholder(q)) continue; const on = norm(q.replace(/^org:/, "")); const ok = node("org:" + on.toLowerCase(), "org", on); nodes.get(ok).edrpou = e.edrpou; edge(ok, rk, "registry_record", null); }
    // Founders are kept under their FULL name (surname, name, patronymic): two people who share surname and name are
    // different nodes. A person query ("Кравченко Дмитро") is linked to each full-name match with a "namesake" edge,
    // so the user sees the candidates and picks by patronymic instead of the graph merging strangers into one person.
    const pq = (e.q || []).filter((x) => /^person:/.test(x)).map((x) => x.slice(7).toLowerCase());
    const big = (e.founders || []).length > 60;  // cooperatives with thousands of members: list only query matches
    nodes.get(rk).n_founders = (e.founders || []).length;
    const people = (list, rel) => { for (const f of list || []) {
      const nm = String(f).split(";")[0].trim(); if (isPlaceholder(nm)) continue;
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
  for (const h of R.hits) {
    if (!msgMap.has(h.m)) msgMap.set(h.m, { m: h.m, s: h.s, t: h.t, u: h.u, x: h.x });
    edge(objFor(h.query), post(h.m), "mentioned_in", h.m, { date: h.t, src: h.s });
  }
  for (const w of R.web) {
    if (/site:/.test(w.query)) continue;
    const wid = node("web:" + w.url, "web", (w.title || w.domain || w.url).slice(0, 80), { url: w.url, text: w.snippet, src: w.domain, engine: w.engine });
    edge(objFor(w.query), wid, "web_mention", null, { src: w.domain });
  }
  for (const w of R.web) {
    if (!/site:reyestr/.test(w.query)) continue;
    const base = w.query.replace(/\s*site:\S+/, "").replace(/"/g, "");
    const wid = node("web:" + w.url, "web", (w.title || w.url).slice(0, 80), { url: w.url, text: w.snippet, src: "reyestr.court.gov.ua", engine: w.engine });
    edge(objFor(base), wid, "court_register_mention", null, { src: "reyestr.court.gov.ua" });
  }
  // media claims: who the press names in connection with a case (quote + URL as evidence); not a registry fact
  for (const ml of R.media) {
    if (isPlaceholder(ml.name_norm)) continue;
    const cid = node("case:" + ml.cause_num.toLowerCase(), "case", "Справа " + ml.cause_num, { ref: ml.cause_num });
    const pid = node("person:" + norm(ml.name_norm).toLowerCase(), "person", norm(ml.name_norm), { position: ml.position });
    edge(pid, cid, "named_by_media", null, { detail: `за даними ЗМІ (${Math.round((ml.confidence || 0) * 100)}%): ${ml.role} — «${(ml.quote || "").slice(0, 160)}» ${ml.url}` });
  }
  // registry hits from the local worker (Ukrainian IP): court register full-text search, NAZK declarations, ЄДР ФОП
  for (const h of R.rhits) {
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
    } else if (h.source === "summons") {
      // a court's notice summoning the person (in-absentia proceedings): the case number ties them to its decisions
      if (p.cause_num) {
        const cid = node("case:" + String(p.cause_num).toLowerCase(), "case", "Справа " + p.cause_num, { ref: p.cause_num, court: p.court });
        edge(who, cid, "summoned", null, { detail: [p.notice_type, p.court, p.date].filter(Boolean).join(" · "), src: "court.gov.ua (оголошення про виклик)" });
        if (p.proc_num && /^\d{17}$/.test(p.proc_num)) edge(cid, node("erdr:" + p.proc_num, "case", "Провадження ЄРДР " + p.proc_num, { ref: p.proc_num, kind: "erdr" }), "proceeding", null);
      }
    } else if (h.source === "edr_fop") {
      const fid = node("fop:" + h.ref, "registry", h.title, { stan: p.stan, text: h.snippet, src: "ЄДР (ФОП)" });
      edge(who, fid, "fop_record", null, { detail: h.snippet, src: "ЄДР" });
    }
  }
  // police wanted list: a record per person already in the graph under the same full name; searching body and article as nodes
  for (const w of R.wanted || []) {
    const short = "person:" + (w.last_name_u + " " + w.first_name_u).toLowerCase();
    if (w.m !== "full" && !nodes.has(short)) continue;
    if (w.m === "full" && !nodes.has("person:" + w.name_u)) continue;
    const pid = node("person:" + w.name_u, "person", [w.last_name_u, w.first_name_u, w.middle_name_u].filter(Boolean).join(" ").toLowerCase()
      .replace(/(^|[\s'’-])(\p{L})/gu, (_, a, b) => a + b.toUpperCase()));
    if (w.m !== "full") edge(short, pid, "namesake", null, { detail: "збіг прізвища та імені з записом розшуку; той самий чи інший — визначають по батькові й дата народження" });
    const who = [w.last_name_u, w.first_name_u, w.middle_name_u].filter(Boolean).join(" ").toLowerCase().replace(/(^|[\s'’-])(\p{L})/gu, (_, a, b) => a + b.toUpperCase());
    const wid = node("wanted:" + w.id, "wanted", "Розшук · " + who, { category: w.category, article: w.article_crim, restraint: w.restraint, ovd: w.ovd, birth_date: w.b, date: w.d,
      src: "Нацполіція (data.gov.ua)" });
    edge(pid, wid, "wanted", null, { detail: [(w.category || "").toLowerCase(), w.article_crim, w.restraint, w.d ? "з " + w.d : "", w.b ? "нар. " + w.b : "",
      "збіг за повним ПІБ — звіряйте дату народження"].filter(Boolean).join(" · "), src: "Нацполіція" });
    if (w.ovd) edge(node("org:" + norm(w.ovd).toLowerCase(), "org", norm(w.ovd), { kind: w.agency }), wid, "searches", null);
    if (w.article) edge(wid, node("article:" + w.article, "article", `ст. ${w.article} КК`), "article", null, { detail: w.article_crim });
  }
  // property in court cases: proceeding nodes, items from arrest rulings, ARMA's arrested assets
  if (R.caseAssets) {
    const A = assetGraph(R.caseAssets);
    for (const n of A.nodes) { const { id, type, label, ...attrs } = n; node(id, type, label, attrs); }
    for (const e of A.edges) edge(e.source, e.target, e.rel, null, { detail: e.detail, src: e.src });
  }
  // declarations' contents: real estate, vehicles, valuables as asset nodes; companies, income sources, lenders, family
  const D = declGraph(R.decl || []);
  for (const n of D.nodes) { const { id, type, label, ...attrs } = n; node(id, type, label, attrs); }
  for (const e of D.edges) edge(e.source, e.target, e.rel, null, { detail: e.detail, src: e.src });
  // analyst-supplied links (the researcher's own knowledge): kept apart from registry facts and labelled as such
  for (const l of R.alinks) {
    const a = node(l.src_kind + ":" + norm(l.src_name).toLowerCase(), l.src_kind, norm(l.src_name));
    const b = node(l.dst_kind + ":" + norm(l.dst_name).toLowerCase(), l.dst_kind, norm(l.dst_name));
    edge(a, b, l.relation, null, { detail: `вказано аналітиком ${l.d || ""}${l.note ? ": " + l.note : ""}`, src: "analyst" });
  }
  // public money: spending.gov.ua transactions to companies in the graph (payer state body -> company, totals per pair);
  // detail/date are (re)assigned so a delta's recomputed totals replace the snapshot's
  for (const r of R.pay) {
    const rk = "registry:" + r.e;
    if (!nodes.has(rk)) continue;
    const pid = node("org:" + norm(r.pn || r.pe).toLowerCase(), "org", norm(r.pn || r.pe), { kind: "state_body", edrpou: r.pe });
    const e = edge(pid, rk, "paid_public_money", null);
    if (e) Object.assign(e, { detail: `${r.n} платежів на ${Math.round(r.total).toLocaleString("uk-UA")} грн, ${r.d0}…${r.d1} (spending.gov.ua)`, src: "spending.gov.ua", date: r.d1 });
  }
  // placeholders that slipped in through paths without a check (objFor of a queued «НЕМАЄ», a delta on an older snapshot)
  for (const [id, n] of nodes) if (isJunk(n)) nodes.delete(id);
  for (const [k, e] of edges) if (!nodes.has(e.source) || !nodes.has(e.target)) edges.delete(k);
  const srcSet = [...new Set([...(base?.sources || []), ...R.msgs.map((x) => x.s)])].sort();
  return { nodes: [...nodes.values()], edges: [...edges.values()], sources: srcSet, generated: new Date().toISOString().slice(0, 10) };
}

// Database clock, read before the rows: rows written after it go into the next delta. Rows committed late by a long
// transaction can carry an earlier created_at, so deltas start DELTA_OVERLAP before this mark.
export const DELTA_OVERLAP = 60 * 60e3;
const dbNow = async (q) => (await q`SELECT now() AS t`)[0].t;
const iso = (t) => new Date(t).toISOString();

// Full build from Neon (~16 MB). `q` is the tagged-template query function (the snapshot script and tests pass their own).
export async function build(q = sql) {
  const wm = iso(await dbNow(q));
  return { ...assemble(await loadRows(q)), wm, built: new Date().toISOString(), full_at: wm };
}

// Rows written since `g.wm` (minus the overlap) merged into g; returns a new graph object sharing g's node/edge objects.
export async function applyDelta(g, q = sql) {
  const wm = iso(await dbNow(q));
  const since = iso(Date.parse(g.wm) - DELTA_OVERLAP);
  const R = await loadRows(q, since);
  const added = Object.values(R).reduce((s, x) => s + x.length, 0);
  return { ...assemble(R, g), wm, built: new Date().toISOString(), full_at: g.full_at || g.wm, delta_rows: added };
}

// ---- snapshot on the GitHub release ----
const GRAPH_BASE = () => (process.env.GRAPH_BASE_URL || "https://github.com/ol-voronin/neo4graph/releases/download/graph-latest/").replace(/\/?$/, "/");
const MANIFEST_TTL = 10 * 60e3;          // look for a newer snapshot at most every 10 min per instance
const SNAPSHOT_MAX_AGE = 24 * 3600e3;    // older than this (manifest.checked) -> build from Neon instead
let snap = null, manifestAt = 0;          // snap = { file, checked, graph }

const fresh24 = (t) => Date.now() - Date.parse(t) <= SNAPSHOT_MAX_AGE;
async function manifest() {
  // `gh release upload --clobber` deletes the old manifest before the new one lands: retry once across that gap
  for (let i = 0; ; i++) {
    const r = await fetch(GRAPH_BASE() + "graph_manifest.json", { cache: "no-store" }).catch((e) => ({ ok: false, status: e.message }));
    if (r.ok) return r.json();
    if (i >= 1) throw new Error(`graph manifest: HTTP ${r.status}`);
    await new Promise((ok) => setTimeout(ok, 1500));
  }
}
async function loadSnapshot(force = false) {
  if (snap && !force && Date.now() - manifestAt < MANIFEST_TTL) return snap;
  let m;
  try { m = await manifest(); } catch (e) {
    // GitHub hiccup: a snapshot this instance already holds stays valid until it is 24 h old; look again in a minute
    if (snap && fresh24(snap.checked)) { manifestAt = Date.now() - MANIFEST_TTL + 60e3; return snap; }
    throw e;
  }
  if (!m.file || !fresh24(m.checked || m.built)) throw new Error(`graph snapshot stale: ${m.checked || m.built}`);
  manifestAt = Date.now();
  if (!snap || snap.file !== m.file) {
    const a = await fetch(GRAPH_BASE() + m.file);
    if (!a.ok) throw new Error(`graph snapshot ${m.file}: HTTP ${a.status}`);
    snap = { file: m.file, graph: JSON.parse(gunzipSync(Buffer.from(await a.arrayBuffer())).toString("utf8")) };
  }
  snap.checked = m.checked;
  // an unchanged re-check moves only the manifest's wm: the file's content is current up to it, so deltas start there
  if (m.wm && !(snap.graph.wm >= m.wm)) snap.graph.wm = m.wm;
  return snap;
}

// ---- one graph per warm instance; the CDN in front caches /api/graph responses for 10 minutes ----
let cache = null;
const pending = [null, null];  // [plain, fresh]
async function load(fresh, q) {
  let s = null;
  try { s = await loadSnapshot(fresh); } catch (e) { console.warn("graph snapshot unavailable, building from Neon:", e.message); }
  if (!s) return index(await build(q), "neon");
  // a newer snapshot supersedes whatever this instance merged before; otherwise keep the (possibly delta-merged) graph
  const base = cache && cache.file === s.file && cache.wm >= s.graph.wm ? cache : s.graph;
  // ?fresh: a registry run just wrote rows; merge what was written since the snapshot instead of rebuilding (a few KB)
  if (fresh) return { ...index(await applyDelta(base, q), "snapshot+delta"), file: s.file };
  return base === cache ? { ...cache, at: Date.now() } : { ...index(base, "snapshot"), file: s.file };
}
// the graph this warm instance already holds, or null; never triggers a load (search uses it to mark hits already in the graph)
export const peekGraph = () => cache;
export function getGraph(fresh = false, q = sql) {  // q: query function (tests pass a stub)
  if (cache && Date.now() - cache.at < (fresh ? 20e3 : MANIFEST_TTL) && (!fresh || cache.origin !== "snapshot")) return Promise.resolve(cache);
  const i = fresh ? 1 : 0;  // a fresh request never rides on a plain load already in flight
  pending[i] ??= load(fresh, q).then((g) => (cache = g)).finally(() => { pending[i] = null; });
  return pending[i];
}
export function index(g, origin = "neon") {
  // snapshots built before the junk sweep in assemble() still carry placeholder nodes
  if (g.nodes.some(isJunk)) { const nodes = g.nodes.filter((n) => !isJunk(n)), keep = new Set(nodes.map((n) => n.id)); g = { ...g, nodes, edges: g.edges.filter((e) => keep.has(e.source) && keep.has(e.target)) }; }
  const byId = new Map(g.nodes.map((n) => [n.id, n])), adj = new Map();
  for (const e of g.edges) for (const x of [e.source, e.target]) (adj.get(x) || adj.set(x, []).get(x)).push(e);
  for (const n of g.nodes) n.deg = (adj.get(n.id) || []).length;
  return { ...g, byId, adj, at: Date.now(), origin };
}
const wire = (e) => (e.msgs.length > 5 ? { ...e, msgs: e.msgs.slice(0, 5), nmsgs: e.msgs.length } : e);
const lite = ({ text, summary, ...n }) => (summary ? { ...n, summary: summary.slice(0, 160) } : n);

// overview: the n most connected non-post nodes and the edges between them
// (placeholders dropped, and nodes with no edge to another candidate: otherwise half the first screen is unconnected dots)
export function overview(g, n = 300) {
  const cand = g.nodes.filter((x) => x.type !== "post" && !isJunk(x)).sort((a, b) => b.deg - a.deg).slice(0, n * 2), cids = new Set(cand.map((x) => x.id)), linked = new Set();
  for (const x of cand) for (const e of g.adj.get(x.id) || []) if (e.source === x.id && cids.has(e.target)) { linked.add(e.source); linked.add(e.target); }
  const top = cand.filter((x) => linked.has(x.id)).slice(0, n), ids = new Set(top.map((x) => x.id)), edges = [];
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
