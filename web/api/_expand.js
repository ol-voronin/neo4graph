import { query } from "./_db.js";
import { getGraph, peekGraph, norm, isPlaceholder } from "./_graph.js";
import { getManifest, searchKey } from "./_lake.js";
import { byCode, byName, peopleOf, rolesOf, card } from "./registry.js";
import { postMentions, decisionTexts, webFound } from "./_mentions.js";

// On-the-fly neighbourhood for an object the graph does not hold yet (a registry hit from /api/search, a judge, a «topic:»
// of post mentions). Same wire format as /api/graph?around=, so the page merges it into what it already draws:
//   registry:<ЄДРПОУ> — lake card + founders / beneficiaries / signers (registry_people) + posts and decision texts naming it
//   fop:<record>, person:<name>, org:<name> — lake card / companies the person has a role in (registry_roles), judge's
//                                             decisions, posts and decision texts
//   topic:<words>     — the posts that mention the words, their channels and the graph entities those posts already link to
// Every part has its own time cap and fails alone; Neon sees a few indexed lookups and at most one capped post scan.
const soft = (p, ms, dflt) => Promise.race([Promise.resolve(p), new Promise((_, no) => setTimeout(() => no(new Error("timeout")), ms))]).catch(() => dflt);
const title = (s) => norm(s).toLowerCase().replace(/(^|[\s'’-])(\p{L})/gu, (_, a, b) => a + b.toUpperCase());
const ROLE_REL = { founder: "founder", beneficiary: "beneficiary", signer: "signer" };
const core = (s) => norm(s).replace(/[«»"“”„]/g, " ").replace(/^(тов|пат|прат|ат|тдв|дп|кп|пп|фоп|товариство з обмеженою відповідальністю|приватне підприємство)\s+/i, "").replace(/\s+/g, " ").trim();

export async function expand(id, label = "", { judge = false } = {}) {
  id = norm(id);
  const i = id.indexOf(":"), kind = id.slice(0, i), key = id.slice(i + 1);
  if (i < 1 || !key) return { error: "bad id" };
  const nodes = new Map(), edges = [], seenE = new Set();
  const node = (n) => { const x = nodes.get(n.id); if (x) { for (const [k, v] of Object.entries(n)) if (x[k] == null && v != null) x[k] = v; return n.id; } nodes.set(n.id, n); return n.id; };
  const edge = (source, target, rel, extra = {}) => { const k = source + "|" + target + "|" + rel; if (source === target || seenE.has(k)) return; seenE.add(k); edges.push({ source, target, rel, msgs: [], ...extra }); };
  const out = { center: id, expanded: true };
  let name = core(label || key);

  // ---- the object itself ----
  if (kind === "topic") {
    name = norm(label || key);
    node({ id, type: "topic", label: name });
  } else if (kind === "registry" && /^\d{8}$/.test(key)) {
    const [hit, m, people] = await Promise.all([soft(byCode(key), 3000, { rows: [] }), soft(getManifest(), 2500, null), soft(peopleOf(key), 3000, [])]);
    const r = hit.rows?.find((x) => x.code === key) || hit.rows?.[0];
    const c = r && m ? card(r, m) : null;
    if (c) name = core(c.name);
    node({ id, type: "registry", label: `ЄДР ${key} · ${c?.name || label || key}`, edrpou: key, stan: c?.stan, full: c?.full_name, region: c?.region, address: c?.address, src: "ЄДР (data lake)" });
    for (const p of people) {
      if (isPlaceholder(p.name)) continue;
      const pid = node({ id: "person:" + norm(p.name).toLowerCase(), type: "person", label: title(p.name) });
      edge(pid, id, ROLE_REL[p.role] || "related", { detail: p.detail, src: "ЄДР (data lake)" });
    }
    out.people = people.length;
  } else if (kind === "fop" || kind === "person" || kind === "org") {
    const m = await soft(getManifest(), 2500, null);
    const type = kind === "org" ? "org" : kind === "fop" ? "registry" : "person";
    node({ id, type, label: kind === "fop" ? "ФОП " + title(name) : kind === "person" ? title(name) : norm(label || key) });
    if (m) {
      const jobs = [];
      if (kind !== "org") {
        // companies the person founded / signs for, under both name orders
        const w = name.split(" ");
        for (const v of [name, w.length >= 2 ? [w.at(-1), ...w.slice(0, -1)].join(" ") : null].filter(Boolean)) jobs.push(soft(rolesOf(v, m), 3000, []));
      }
      // the lake's own card (a ФОП record, an org from ProZorro)
      jobs.push(soft(byName(name, m, kind !== "org"), 3000, { rows: [] }).then((r) => (r.rows || []).filter((x) => x.id === id).map((x) => ({ card: card(x, m) }))));
      for (const r of (await Promise.all(jobs)).flat()) {
        if (r.card) { const n = nodes.get(id); Object.assign(n, { stan: r.card.stan, region: r.card.region, address: r.card.address, edrpou: r.card.code_type === "edrpou" ? r.card.code : undefined, src: r.card.sources.join(", ") }); continue; }
        if (searchKey(r.name, m) !== searchKey(name, m) && !searchKey(r.name, m).startsWith(searchKey(name, m))) continue;
        const rid = node({ id: "registry:" + r.code, type: "registry", label: `ЄДР ${r.code} · ${r.org || ""}`, edrpou: r.code, stan: r.stan, src: "ЄДР (data lake)" });
        // a full-name match (with patronymic) is this person; a shorter query only names a namesake candidate
        const pid = searchKey(r.name, m) === searchKey(name, m) ? id : node({ id: "person:" + norm(r.name).toLowerCase(), type: "person", label: title(r.name) });
        if (pid !== id) edge(id, pid, "namesake", { detail: "збіг прізвища та імені у ЄДР; той самий чи інший — визначає по батькові" });
        edge(pid, rid, ROLE_REL[r.role] || "related", { src: "ЄДР (data lake)" });
      }
    }
    // a judge (the search said so): decisions they signed (no index on judge: one capped pass, newest first)
    if (kind === "person" && judge) {
      const dec = await soft(query(`SELECT doc_id, cause_num, judgment_form f, adjudication_date::text d, court, judge, doc_url FROM court_decisions
                                    WHERE lower(judge) = $1 ORDER BY adjudication_date DESC NULLS LAST LIMIT 25`, [key]), 2500, []);
      for (const d of dec) {
        const did = decisionNode(node, d);
        edge(id, did, "judge_of", { date: d.d });
        if (d.cause_num) edge(did, caseNode(node, d.cause_num), "decision_of", { date: d.d });
      }
      if (dec.length) nodes.get(id).position = "суддя" + (dec[0].court ? ", " + dec[0].court : "");
    }
  } else return { error: "expand: тип " + kind + " не підтримується", center: id, nodes: [], edges: [] };

  // ---- what the texts say: posts and decision texts naming it ----
  const isTopic = kind === "topic";
  // a person's posts: surname + first name when known (stems), otherwise the whole name
  const textQ = kind === "person" || kind === "fop" ? name.split(" ").slice(0, 2).join(" ") : name;
  const [posts, texts, web] = await Promise.all([
    soft(postMentions(textQ, { limit: isTopic ? 30 : 12, ms: 2800 }), 3200, { total: 0, items: [] }),
    isTopic ? { items: [] } : soft(decisionTexts(textQ, { limit: 8 }), 3000, { items: [] }),
    soft(webFound(label || name), 2000, { items: [] }),
  ]);
  // a named object: only posts that carry every word of the name (surname + first name; «Рошен-Чернігів»), not the surname alone
  if (!isTopic && (posts.words || []).length > 1) { posts.items = (posts.items || []).filter((p) => p.all_words); posts.total = posts.all || posts.items.length; }
  const ev = {};
  for (const p of posts.items || []) {
    const pid = node({ id: p.id, type: "post", label: (p.snippet || "").slice(0, 70) + "…", src: p.source, date: p.date, url: p.url, text: p.snippet });
    edge(id, pid, "mentioned_in", { msgs: [p.msg], date: p.date, src: p.source });
    ev[p.msg] = { date: p.date, src: p.source, text: p.snippet, url: p.url };
    if (isTopic) {
      const cid = node({ id: "channel:" + p.source, type: "channel", label: p.src || p.source });
      edge(pid, cid, "published_in", { date: p.date });
    }
  }
  for (const d of texts.items || []) {
    const did = decisionNode(node, d);
    edge(id, did, "court_register_mention", { detail: d.snippet, date: d.d });
    if (d.cause_num) edge(did, caseNode(node, d.cause_num), "decision_of", { date: d.d });
  }
  for (const w of web.items || []) edge(id, node({ id: w.id, type: "web", label: w.label.slice(0, 80), url: w.url, text: w.snippet, src: w.sub }), "web_mention", { src: w.sub });
  if (isTopic) {
    // channels: topic -> channel weighted by posts; co-mentioned entities: what the graph already links to those posts
    const byCh = new Map();
    for (const p of posts.items || []) byCh.set(p.source, (byCh.get(p.source) || 0) + 1);
    for (const [s, n] of byCh) edge(id, "channel:" + s, "mentioned_in", { detail: `постів: ${n}`, w: n });
    const g = peekGraph() || await soft(getGraph(), 2500, null);
    const co = new Map();
    for (const p of posts.items || []) for (const e of g?.adj?.get(p.id) || []) {
      const other = e.source === p.id ? e.target : e.source, n = g.byId.get(other);
      if (!n || n.type === "post") continue;
      co.set(other, (co.get(other) || 0) + 1);
      node({ ...n, text: undefined, summary: undefined });
      edge(other, p.id, e.rel, { msgs: (e.msgs || []).slice(0, 3), date: e.date, src: e.src });
    }
    for (const [o, n] of [...co].sort((a, b) => b[1] - a[1]).slice(0, 40)) edge(o, id, "co_mentioned", { detail: `разом у ${n} пост(ах)` });
    out.co_mentioned = co.size;
  }
  out.posts = { total: posts.total || 0, capped: posts.capped, words: posts.words, items: (posts.items || []).slice(0, isTopic ? 30 : 12) };
  out.nodes = [...nodes.values()];
  out.edges = edges.filter((e) => nodes.has(e.source) && nodes.has(e.target));
  out.ev = ev;
  out.total = out.shown = out.nodes.length;
  return out;
}

function decisionNode(node, d) {
  return node({ id: "decision:" + d.doc_id, type: "decision", label: `${d.f || "Рішення"} ${d.d || ""}`.trim(), doc_id: d.doc_id, form: d.f, date: d.d, court: d.court, judge: d.judge, url: d.doc_url, text: d.snippet });
}
function caseNode(node, num) {
  return node({ id: "case:" + num.toLowerCase(), type: "case", label: "Справа " + num, ref: num });
}
