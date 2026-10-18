import { sql, json } from "./_db.js";

// GET /api/graph -> entity graph built from the database (persons, orgs, cases, assets, ЄДР records, evidence posts).
const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
const caseKey = (label) => "case:" + norm(label).replace(/[«»"“”„]/g, "").replace(/^(операція|операция|справа)\s+/i, "").toLowerCase();

export default async function handler(req, res) {
  try {
    const [persons, orgs, cases, assets, edr, msgs] = await Promise.all([
      sql`SELECT message_id m, source_id s, ts_published::date t, name_norm n, name_as_written w, role r, position p, org o FROM ee_persons`,
      sql`SELECT message_id m, source_id s, ts_published::date t, name n, edrpou e, kind k, role r FROM ee_orgs`,
      sql`SELECT message_id m, source_id s, ts_published::date t, ref, nickname nick, agency a, stage st, summary sum FROM ee_cases`,
      sql`SELECT message_id m, source_id s, ts_published::date t, type ty, description d, linked_person lp FROM ee_assets`,
      sql`SELECT edrpou, short_name name, name "full", stan, founders, beneficiaries benef, signers, matched_queries q FROM edr_orgs`,
      sql`SELECT e.message_id m, mm.source_id s, mm.ts_published::date t, mm.url u, left(regexp_replace(mm.text, '\s+', ' ', 'g'), 260) x
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
    for (const e of edr) {
      const rk = node("registry:" + e.edrpou, "registry", `ЄДР ${e.edrpou} · ${e.name}`, { stan: e.stan, full: e.full, edrpou: e.edrpou });
      for (const q of e.q || []) { const ok = node("org:" + norm(q).toLowerCase(), "org", norm(q)); nodes.get(ok).edrpou = e.edrpou; edge(ok, rk, "registry_record", null); }
      const people = (list, rel) => { for (const f of list || []) {
        const nm = String(f).split(";")[0].trim(); if (!nm) continue;
        const tail = String(f).split(";")[1] || "";
        if (/\d{8}/.test(tail)) { edge(node("org:" + nm.toLowerCase(), "org", nm), rk, "founder", null); continue; }
        const short = nm.split(/\s+/).slice(0, 2).join(" ");
        const title = short.replace(/[^\s'’-]+/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase());
        edge(node("person:" + short.toLowerCase(), "person", title), rk, rel, null, { detail: String(f) });
      } };
      people(e.founders, "founder"); people(e.benef, "beneficiary"); people(e.signers, "signer");
    }
    // query-driven findings: corpus hits and web mentions attach to the object that was searched
    const [hits, web] = await Promise.all([
      sql`SELECT h.query, h.message_id m, h.where_found w, mm.source_id s, mm.ts_published::date t, mm.url u, left(regexp_replace(mm.text,'\s+',' ','g'),260) x
          FROM query_hits h JOIN messages mm ON mm.id = h.message_id ORDER BY mm.ts_published DESC LIMIT 2000`.catch(() => []),
      sql`SELECT query, url, title, snippet, domain, engine FROM web_mentions ORDER BY fetched_at DESC LIMIT 1000`.catch(() => []),
    ]);
    const objFor = (qq) => {
      const k = qq.toLowerCase().replace(/\s+/g, " ").trim();
      const cand = ["person:" + k, "org:" + k];
      for (const c of cand) if (nodes.has(c)) return c;
      const m = [...nodes.keys()].find((id) => (id.startsWith("person:") || id.startsWith("org:")) && id.slice(id.indexOf(":") + 1).startsWith(k.split(" ")[0]));
      return m || node("person:" + k, /^(тов|пат|прат|ат|дп|кп)\b/i.test(k) ? "org" : "person", qq.replace(/\b\w/gu, (c) => c.toUpperCase()));
    };
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
    const srcSet = [...new Set(msgs.map((x) => x.s))].sort();
    return json(res, 200, { nodes: [...nodes.values()], edges: [...edges.values()], sources: srcSet, generated: new Date().toISOString().slice(0, 10) });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
