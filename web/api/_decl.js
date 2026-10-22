// Contents of NAZK declarations (decl_items, migration 023) -> graph nodes and edges, shared by the full graph build
// (_graph.js) and on-the-fly neighbourhoods (_expand.js). One asset node per owner + thing across years (a flat declared
// in 2022 and 2024 is one node; the edge lists the years). Companies with a Ukrainian code become registry:<ЄДРПОУ>
// nodes; foreign companies (Latin names: their 8-digit numbers are foreign registry numbers, not ЄДРПОУ) stay org:<name>.
export const DECL_COLS = `decl_id, year, declarant, step, kind, label, owner, rel_name, rel_code, rel_kind, value::float value, currency, since, region`;

const ASSET = { real_estate: "нерухомість", unfinished: "незавершене будівництво", vehicle: "транспорт", movable: "цінне майно", intangible: "нематеріальний актив" };
const ORG_REL = { corporate: "corporate_rights", beneficial: "beneficial_owner", securities: "securities", membership: "member", account: "bank_account" };
const title = (s) => String(s || "").toLowerCase().replace(/(^|[\s'’-])(\p{L})/gu, (_, a, b) => a + b.toUpperCase());
const money = (v, c) => (v == null ? "" : `${Math.round(v).toLocaleString("uk-UA")} ${c ? c.split(" ")[0] : "грн"}`);

export function declGraph(rows) {
  const nodes = new Map(), edges = new Map();
  const node = (id, type, label, attrs = {}) => { if (!nodes.has(id)) nodes.set(id, { id, type, label, ...attrs }); return id; };
  const edge = (source, target, rel, year, detail, src = "декларація НАЗК") => {
    if (source === target) return;
    const k = source + "|" + target + "|" + rel;
    const e = edges.get(k) || edges.set(k, { source, target, rel, years: new Set(), details: new Set(), src }).get(k);
    if (year) e.years.add(year);
    if (detail) e.details.add(detail);
  };
  const person = (name) => node("person:" + String(name).toLowerCase().replace(/\s+/g, " ").trim(), "person", title(name));
  const org = (name, code) => {
    const foreign = !/[а-яіїєґ]/i.test(name || "");
    if (code && !foreign) return node("registry:" + code, "registry", `ЄДР ${code} · ${name}`, { edrpou: code });
    return node("org:" + String(name).toLowerCase().replace(/\s+/g, " ").trim(), "org", name, foreign && code ? { foreign_code: code } : {});
  };
  for (const r of rows) {
    const decl = person(r.declarant);
    const owner = r.owner ? person(r.owner) : decl;
    const y = r.year;
    if (r.kind === "family") { if (r.rel_name) edge(decl, person(r.rel_name), "family", y, r.label.split(":")[0]); continue; }
    if (ASSET[r.kind]) {
      const id = node("asset:" + (r.owner || r.declarant) + "|" + r.kind + "|" + r.label.toLowerCase(), "asset", r.label,
        { kind: ASSET[r.kind], region: r.region, since: r.since, src: "декларація НАЗК" });
      edge(owner, id, "owns", y, [r.rel_name, r.since ? "з " + r.since : "", money(r.value, r.currency)].filter(Boolean).join(", "));
      continue;
    }
    if (!r.rel_name) continue;  // cash, expenses: no counterparty to draw
    const other = r.rel_kind === "person" ? person(r.rel_name) : org(r.rel_name, r.rel_code);
    if (ORG_REL[r.kind]) edge(owner, other, ORG_REL[r.kind], y, [r.label !== r.rel_name ? r.label : "", money(r.value, r.currency)].filter(Boolean).join(", "));
    else if (r.kind === "income") edge(other, owner, "income", y, `${r.label}: ${money(r.value, r.currency)}`);
    else if (r.kind === "money") edge(owner, other, /позич|позик|поворотн/i.test(r.label) ? "loan_given" : "deposit", y, `${r.label}: ${money(r.value, r.currency)}`);
    else if (r.kind === "liability") edge(other, owner, "creditor", y, `${r.label}: ${money(r.value, r.currency)}`);
  }
  const out = [...edges.values()].map((e) => ({ source: e.source, target: e.target, rel: e.rel, src: e.src,
    detail: `${[...e.years].sort().join(", ")}${e.details.size ? ": " + [...e.details].slice(0, 3).join("; ") : ""}` }));
  return { nodes: [...nodes.values()], edges: out };
}
