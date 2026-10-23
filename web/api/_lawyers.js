import { query } from "./_db.js";
import { nameLink } from "./_identity.js";

// Lawyers named in court decision texts (person_case_links, migration 031, src/person_cases.py): «адвоката Бондика
// Валерія Анатолійовича» resolved against the register of lawyers (ЄРАУ). The link rests on the name alone, so it follows
// the rule in _identity.js: one candidate in the register ('можливо') -> a probable edge lawyer -> case with the mention
// and the decision as evidence; several ('неоднозначно') or none -> no edge, the case card lists them as text.
// Shared by the full graph build (_graph.js), the live overlay of /api/graph?around= (graph.js), on-the-fly
// neighbourhoods (_expand.js) and the case / person cards (node.js).
export const LCOLS = `cause_num, doc_id, role, mention, match_level, candidates, certainty, person_id, ref_id, person_name`;
const title = (s) => String(s || "").toLowerCase().replace(/(^|[\s'’-])(\p{L})/gu, (_, a, b) => a + b.toUpperCase());
const DOC = (id) => "https://reyestr.court.gov.ua/Review/" + id;

// rows for some cases (case:<num> ids or bare numbers) and / or some people (person:<name> ids)
export function lawyerRows({ cases = [], persons = [] } = {}, q = query) {
  const cs = cases.map((c) => String(c).replace(/^case:/, "").toLowerCase()).filter(Boolean), ps = persons.filter(Boolean);
  if (!cs.length && !ps.length) return Promise.resolve([]);
  return q(`SELECT ${LCOLS} FROM person_case_links WHERE lower(cause_num) = ANY($1::text[]) OR person_id = ANY($2::text[]) ORDER BY cause_num, person_name LIMIT 400`, [cs, ps])
    .catch(() => []);
}

// rows -> graph: person (lawyer) -> case edges, one per pair, the mentions and decisions of every row as evidence
export function lawyerGraph(rows) {
  const nodes = new Map(), edges = new Map();
  for (const r of rows || []) {
    if (!r.cause_num || !r.person_id || r.certainty !== "можливо") continue;
    const cid = "case:" + r.cause_num.toLowerCase();
    if (!nodes.has(cid)) nodes.set(cid, { id: cid, type: "case", label: "Справа " + r.cause_num, ref: r.cause_num });
    if (!nodes.has(r.person_id)) nodes.set(r.person_id, { id: r.person_id, type: "person", label: title(r.person_name), position: "адвокат", erau_id: r.ref_id, src: "Єдиний реєстр адвокатів (НААУ)" });
    const k = r.person_id + "|" + cid;
    const e = edges.get(k) || edges.set(k, { source: r.person_id, target: cid, n: r.candidates, mentions: new Set(), docs: new Set() }).get(k);
    e.mentions.add(r.mention); e.docs.add(r.doc_id);
  }
  const out = [...edges.values()].map((e) => {
    const docs = [...e.docs], { rel, attrs } = nameLink("lawyer_in", e.n, {
      detail: `${[...e.mentions].slice(0, 2).map((m) => "«" + m + "»").join(", ")} у тексті рішення ${docs.slice(0, 3).join(", ")}; у реєстрі адвокатів одна особа з таким ПІБ`,
      doc_id: docs[0], url: DOC(docs[0]), docs: docs.slice(0, 5), src: "ЄДРСР + ЄРАУ" }, "адвокатів");
    return { source: e.source, target: e.target, rel, msgs: [], ...attrs };
  });
  return { nodes: [...nodes.values()], edges: out };
}

// rows -> what a card lists (all certainties): [{ cause_num, mention, certainty, candidates, person_id, person_name, doc_id, url }]
export function lawyerList(rows) {
  const seen = new Set(), out = [];
  for (const r of rows || []) {
    const k = r.cause_num + "|" + (r.person_id || r.mention.toLowerCase());
    if (seen.has(k)) continue;
    seen.add(k);
    out.push({ cause_num: r.cause_num, mention: r.mention, certainty: r.certainty, candidates: r.candidates, match_level: r.match_level,
      person_id: r.person_id || undefined, person_name: r.person_name || undefined, erau_id: r.ref_id || undefined, doc_id: r.doc_id, url: DOC(r.doc_id) });
  }
  return out;
}
