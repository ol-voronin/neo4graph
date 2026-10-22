import { query } from "./_db.js";

// Property tied to court cases (migrations 024–025): items named in decisions (case_assets) and ARMA's register of
// arrested assets (arma_assets), joined to cases through case numbers and ЄРДР proceeding numbers. The register
// depersonalises suspects, so a person reaches these items only through a case they are named in (summons notice, court
// register hit by name, media) — the graph says so on every edge. Shared by the full graph build and _expand.js.
const ACTION = { arrested: "arrested", arrest_lifted: "arrest_lifted", confiscated: "confiscated", seized: "seized", returned: "returned", named: "named_in_case" };
const KIND = { vehicle: "транспорт", real_estate: "нерухомість", land: "земля", money: "кошти", account: "рахунки", shares: "акції / частки", company: "компанія",
  weapon: "зброя", other: "майно", vessel: "судно", railcar: "вагони", goods: "товари" };
const caseId = (c) => "case:" + String(c).toLowerCase().trim();
const erdrId = (e) => "erdr:" + e;

// rows: {links: [{cause, erdr}], items: case_assets rows, arma: arma_assets rows}
export function assetGraph({ links = [], items = [], arma = [] }) {
  const nodes = new Map(), edges = new Map();
  const node = (id, type, label, attrs = {}) => { if (!nodes.has(id)) nodes.set(id, { id, type, label, ...attrs }); return id; };
  const edge = (source, target, rel, detail, src) => { const k = source + "|" + target + "|" + rel; if (source !== target && !edges.has(k)) edges.set(k, { source, target, rel, detail, src }); };
  const caseNode = (c) => node(caseId(c), "case", "Справа " + c, { ref: c });
  const erdrNode = (e) => node(erdrId(e), "case", "Провадження ЄРДР " + e, { ref: e, kind: "erdr" });
  for (const l of links) if (l.cause && l.erdr) edge(caseNode(l.cause), erdrNode(l.erdr), "proceeding", "номер провадження в тексті рішення", "ЄДРСР");
  for (const r of items) {
    // one node per thing per proceeding: rulings of the same proceeding name the same flat many times
    const anchor = r.erdr?.length ? erdrNode(r.erdr[0]) : r.cause_num ? caseNode(r.cause_num) : null;
    if (!anchor) continue;
    if (r.cause_num) caseNode(r.cause_num);
    const id = node("asset:" + anchor + "|" + r.label.toLowerCase(), "asset", r.label, { kind: KIND[r.kind] || r.kind, year: r.year, area: r.area,
      reg_number: r.reg_number, cadastral: r.cadastral, bank: r.bank, edrpou: r.edrpou, src: "судове рішення (ЄДРСР)" });
    edge(anchor, id, ACTION[r.action] || "named_in_case",
      [r.holder ? "власник у рішенні: " + r.holder : "", r.cause_num ? "ухвала у справі " + r.cause_num : "", r.decision_date ? "від " + String(r.decision_date).slice(0, 10) : ""].filter(Boolean).join(", "),
      "ЄДРСР");
    if (r.cause_num && anchor !== caseId(r.cause_num)) edge(caseId(r.cause_num), anchor, "proceeding", "номер провадження в тексті рішення", "ЄДРСР");
  }
  for (const a of arma) {
    const id = node("arma:" + a.asset_id, "asset", [a.description, a.brand, a.model, a.year].filter(Boolean).join(" ").replace(/\s+/g, " ").trim().slice(0, 120) || "Актив АРМА",
      { kind: KIND[a.kind] || a.kind, area: a.area, region: a.region, valuation: a.valuation, state: a.state, src: "реєстр арештованих активів АРМА" });
    const detail = [a.state, a.valuation ? `оцінка ${Math.round(a.valuation).toLocaleString("uk-UA")} грн` : ""].filter(Boolean).join(", ");
    for (const e of a.erdr || []) edge(erdrNode(e), id, "arma_asset", detail, "АРМА");
    for (const c of a.docnums || []) if (nodes.has(caseId(c))) edge(caseId(c), id, "arma_asset", detail, "АРМА");
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}

const ITEM_COLS = `doc_id, cause_num, erdr, decision_date::text decision_date, kind, label, action, holder, year, area::float area, reg_number, cadastral, bank, edrpou`;
const ARMA_COLS = `asset_id, description, kind, brand, model, year, area::float area, region, docnums, erdr, state, valuation::float valuation`;
const soft = (p) => Promise.resolve(p).catch((e) => { console.warn("assets:", e.message); return []; });

// everything for a set of case numbers (lower case): their ЄРДР numbers, items named in those proceedings, ARMA assets
export async function assetsForCases(causes, q = query) {
  if (!causes.length) return { links: [], items: [], arma: [] };
  const links = await soft(q(`SELECT DISTINCT lower(cause_num) cause, e erdr FROM court_decisions, unnest(erdr) e WHERE lower(cause_num) = ANY($1)`, [causes]));
  const erdr = [...new Set(links.map((l) => l.erdr))];
  const [items, arma] = await Promise.all([
    soft(q(`SELECT ${ITEM_COLS} FROM case_assets WHERE lower(cause_num) = ANY($1) OR erdr && $2::text[] LIMIT 300`, [causes, erdr])),
    soft(q(`SELECT ${ARMA_COLS} FROM arma_assets WHERE docnums && $1::text[] OR erdr && $2::text[] LIMIT 200`, [causes, erdr])),
  ]);
  // rulings of the same proceeding have their own case numbers: link them too, so the items hang off one proceeding
  return { links, items, arma };
}

// the whole set, for the full graph build: every proceeding named in a decision text, every item, ARMA assets that
// share a case number or proceeding with a decision we hold
export async function allAssets(q) {
  const [links, items, arma] = await Promise.all([
    soft(q`SELECT DISTINCT lower(cause_num) cause, e erdr FROM court_decisions, unnest(erdr) e WHERE erdr IS NOT NULL AND cause_num IS NOT NULL
           AND (lower(cause_num) IN (SELECT lower(cause_num) FROM case_assets) OR e IN (SELECT unnest(erdr) FROM case_assets)
                OR e IN (SELECT unnest(erdr) FROM arma_assets))`),
    soft(q`SELECT doc_id, cause_num, erdr, decision_date::text decision_date, kind, label, action, holder, year, area::float area, reg_number, cadastral, bank, edrpou FROM case_assets`),
    soft(q`SELECT asset_id, description, kind, brand, model, year, area::float area, region, docnums, erdr, state, valuation::float valuation FROM arma_assets a
           WHERE a.erdr && (SELECT array_agg(DISTINCT e) FROM court_decisions, unnest(erdr) e)
              OR a.docnums && (SELECT array_agg(DISTINCT lower(cause_num)) FROM court_decisions WHERE cause_num IS NOT NULL) LIMIT 5000`),
  ]);
  return { links, items, arma };
}
