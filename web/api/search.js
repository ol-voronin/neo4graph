import { query, json, CAUSE_RE } from "./_db.js";
import { norm, caseKey, isPlaceholder, peekGraph } from "./_graph.js";
import { ukVariants } from "./_text.js";
import { getManifest, searchKey, coreKey } from "./_lake.js";
import { byName, byCode, byAddress, rolesOf, card } from "./registry.js";
import { postMentions, decisionTexts, findJudges, webFound } from "./_mentions.js";
import { findPlaces } from "./_places.js";
import { qkey } from "./feedback.js";

// GET /api/search?q=...&kind=all|person|org|fop|code|case|place|channel|post[&part=main|mentions][&src=graph,registry,...]
// Searches everything, not only what is already in the graph. Sources run in parallel, each under its own time cap, and the
// answer is grouped: graph (names already in the graph), registry (the lake's ЄДР / ФОП / ProZorro index: names, codes,
// addresses, people's roles), courts (case numbers, judges, decision texts), mentions (posts that mention the words, plus web
// pages found earlier), places (oblasts + KATOTTG gazetteer), channels. `results` is the flat top 20 across groups
// (exact identifiers, then graph, registry, courts, places, channels, the mentions topic). The page asks for part=main first and
// part=mentions (the one sequential scan) a little later; without `part` both run.
// Trigram indexes and the search_names view come from db/migrations/013_search_trgm.sql; until it is applied, plain LIKE is used.
const KINDS = ["all", "person", "org", "fop", "code", "case", "place", "channel", "post"];
const like = (s) => s.replace(/[\\%_]/g, "\\$&");
// names compare without quotes and the legal-form prefix: «ГАЗ-МДС» is an exact match for ТОВ "ГАЗ-МДС"
const bare = (s) => norm(s).toLowerCase().replace(/[«»"“”„]/g, "").replace(/^(тов|пат|прат|ат|тдв|дп|кп|пп|фг|го|бф|ооо|фоп)\s+/, "").trim();
const tierOf = (k, ...names) => { k = bare(k); const b = names.filter(Boolean).map(bare); return b.includes(k) ? 0 : b.some((x) => x.startsWith(k)) ? 1 : 2; };
const REG = { edr_fop: "fop:", yc_fop: "fopx:", yc_org: "orgx:" };

let caps = null;
export async function getCaps() {
  if (!caps || Date.now() - caps.at > 300e3)
    caps = { at: Date.now(), ...(await query(`SELECT to_regclass('search_names') IS NOT NULL AS names, EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') AS trgm`))[0] };
  return caps;
}

export function detect(q) {
  if (/^https?:\/\//i.test(q)) return "url";
  if (/^\d{1,4}\/\d*(?:\/[\w/-]*)?$/.test(q)) return "case";
  if (/^\d{8}$|^\d{10}$/.test(q)) return "code";
  if (/^\d{17}$/.test(q)) return "erdr";
  return "all";
}

export function sources(q, C) {
  const k = norm(q).toLowerCase(), P = [k, "%" + like(k) + "%"];
  // $1 = query, $2 = substring pattern (every query references both: Postgres rejects unreferenced parameters).
  // With pg_trgm the GIN index serves both LIKE and <% (word similarity); without it, sim is just "starts with".
  const cond = (x) => (C.trgm ? `(${x} LIKE $2 OR $1::text <% ${x})` : `${x} LIKE $2`);
  const sim = (x) => (C.trgm ? `word_similarity($1::text, ${x})` : `(left(${x}, length($1::text)) = $1::text)::int`);
  const S = {
    names: async (kinds) => {
      const rows = C.names
        ? await query(`SELECT kind, key, label, n, ${sim("key")} sim FROM search_names WHERE kind = ANY($3::text[]) AND ${cond("key")} ORDER BY key = $1::text DESC, left(key, length($1::text)) = $1::text DESC, sim DESC, n DESC LIMIT 20`, [...P, kinds])
        : await query(`SELECT kind, lower(label) key, max(label) label, count(*)::int n, 0 sim FROM (
                         SELECT 'person' kind, name_norm label FROM ee_persons WHERE lower(name_norm) LIKE $2 UNION ALL SELECT 'org', name FROM ee_orgs WHERE lower(name) LIKE $2) x
                       WHERE kind = ANY($3::text[]) GROUP BY 1, 2 ORDER BY lower(label) = $1::text DESC, n DESC LIMIT 20`, [...P, kinds]);
      return rows.filter((r) => !isPlaceholder(r.label)).map((r) => ({ id: r.kind === "case" ? caseKey(r.label) : r.kind + ":" + norm(r.key), type: r.kind, label: r.kind === "case" ? norm(r.label).replace(/[«»"“”„]/g, "") : norm(r.label), n: r.n, sim: r.sim, tier: tierOf(k, r.label) }));
    },
    edr: async () => {
      const h = "lower(coalesce(short_name, '') || ' ' || name)";
      const rows = await query(`SELECT edrpou, short_name, name, stan, ${sim(h)} sim FROM edr_orgs WHERE ${cond(h)} ORDER BY sim DESC LIMIT 20`, P);
      return rows.map((r) => ({ id: "registry:" + r.edrpou, type: "registry", label: r.short_name || r.name, sub: `ЄДРПОУ ${r.edrpou}${r.stan ? " · " + r.stan : ""}`, sim: r.sim, tier: tierOf(k, r.short_name, r.name) }));
    },
    reg: async (src) => {
      const rows = await query(`SELECT DISTINCT ON (source, ref) source, ref, title, snippet, ${sim("lower(title)")} sim FROM registry_hits
                                WHERE source IN ('edr_fop', 'yc_fop', 'yc_org') AND source = ANY($3::text[]) AND ${cond("lower(title)")} ORDER BY source, ref LIMIT 40`, [...P, src]);
      return rows.map((r) => ({ id: REG[r.source] + r.ref, type: "registry", label: r.title, sub: (r.snippet || "").slice(0, 80), sim: r.sim, tier: tierOf(k, r.title, (r.title || "").replace(/^ФОП\s+/i, "")) }));
    },
    code: async () => {
      const t = norm(q);
      if (t.length === 8) {
        const [edr, hits, ee] = await Promise.all([
          query(`SELECT edrpou, short_name, name, stan FROM edr_orgs WHERE edrpou = $1`, [t]),
          query(`SELECT DISTINCT ON (source, ref) source, ref, title FROM registry_hits WHERE payload ? 'edrpou' AND payload->>'edrpou' = $1 AND source IN ('yc_org', 'edr_role') LIMIT 10`, [t]).catch(() => []),
          query(`SELECT name, count(*)::int n FROM ee_orgs WHERE edrpou = $1 GROUP BY 1 ORDER BY 2 DESC LIMIT 5`, [t]).catch(() => []),
        ]);
        return [...edr.map((r) => ({ id: "registry:" + r.edrpou, type: "registry", label: r.short_name || r.name, sub: `ЄДРПОУ ${r.edrpou}${r.stan ? " · " + r.stan : ""}`, tier: 0 })),
          ...hits.map((r) => ({ id: r.source === "yc_org" ? "orgx:" + r.ref : "registry:" + t, type: "registry", label: r.title || "ЄДР " + t, sub: "ЄДРПОУ " + t, tier: edr.length ? 1 : 0 })),
          ...ee.map((r) => ({ id: "org:" + norm(r.name).toLowerCase(), type: "org", label: norm(r.name), sub: "ЄДРПОУ " + t, n: r.n, tier: edr.length ? 1 : 0 }))];
      }
      // ІПН / РНОКПП (10 digits): ФОП records and ЄДР founder lines that carry it
      const pat = "%" + t + "%";
      const [fop, edr] = await Promise.all([
        query(`SELECT DISTINCT ON (source, ref) source, ref, title, snippet, payload FROM registry_hits WHERE source IN ('edr_fop', 'yc_fop', 'tax_reg') AND (payload::text LIKE $1 OR snippet LIKE $1) LIMIT 10`, [pat]).catch(() => []),
        query(`SELECT edrpou, short_name, name FROM edr_orgs WHERE (founders || signers || beneficiaries)::text LIKE $1 LIMIT 10`, [pat]),
      ]);
      // tax_reg: the ДПС registration record the local worker fetched for this number -> the named person
      return [...fop.map((r) => (r.source === "tax_reg" && r.payload?.name
          ? { id: "person:" + norm(r.payload.name).toLowerCase(), type: "person", label: norm(r.payload.name), sub: `ДПС: ІПН ${t} · ${r.payload.stan || ""}${r.snippet ? " · " + r.snippet : ""}`, tier: 0, exact: true }
          : { id: REG[r.source] + r.ref, type: "registry", label: r.title, sub: "ІПН " + t, tier: 0 })),
        ...edr.map((r) => ({ id: "registry:" + r.edrpou, type: "registry", label: r.short_name || r.name, sub: `ІПН ${t} у записі ЄДР ${r.edrpou}`, tier: 0 }))];
    },
    cases: async () => {
      const t = norm(q), out = [];
      if (CAUSE_RE.test(t)) out.push(...(await query(`SELECT cause_num, count(*)::int n, min(adjudication_date)::text d0, max(adjudication_date)::text d1 FROM court_decisions WHERE cause_num = $1 GROUP BY 1`, [t])).map((r) => ({ ...r, tier: 0 })));
      if (/^\d{1,4}\/\d*/.test(t)) out.push(...(await query(`SELECT cause_num, count(*)::int n, min(adjudication_date)::text d0, max(adjudication_date)::text d1 FROM court_decisions
                                                            WHERE cause_num LIKE $1 AND cause_num <> $2 GROUP BY 1 ORDER BY 1 LIMIT 12`, [like(t) + "%", t])).map((r) => ({ ...r, tier: 1 })));
      return out.map((r) => ({ id: "case:" + r.cause_num.toLowerCase(), type: "case", label: "Справа " + r.cause_num, cause_num: r.cause_num, n: r.n,
        sub: `${r.n} рішень · ${r.d0 || "?"}…${r.d1 || "?"}`, tier: r.tier }));
    },
    place: async () => {
      const rows = await query(`SELECT region, count(*)::int n, ${sim("lower(region)")} sim FROM extracted_events WHERE region IS NOT NULL AND ${cond("lower(region)")}
                                GROUP BY region ORDER BY sim DESC, n DESC LIMIT 20`, P);
      return rows.map((r) => ({ id: "place:" + norm(r.region).toLowerCase(), type: "place", label: norm(r.region), sub: `подій: ${r.n}`, n: r.n, sim: r.sim, tier: tierOf(k, r.region) }));
    },
    channel: async () => {
      const rows = await query(`SELECT id, name, country, ${sim("lower(name)")} sim FROM sources WHERE lower(id) LIKE $2 OR ${cond("lower(name)")} ORDER BY sim DESC LIMIT 20`, P);
      return rows.map((r) => ({ id: "channel:" + r.id, type: "channel", label: r.name, sub: r.id + (r.country ? " · " + r.country : ""), sim: r.sim, tier: tierOf(k, r.name, r.id, r.id.replace(/^\w+:/, "")) }));
    },
    // posts: no text index; scan the most recent 20k messages only, newest first; Russian spelling variants too
    post: async () => {
      if (k.length < 3) return [];
      const rows = await query(`SELECT id, source_id, ts_published::date::text t, left(regexp_replace(text, '[[:space:]]+', ' ', 'g'), 200) x
                                FROM (SELECT id, source_id, ts_published, text FROM messages ORDER BY ts_published DESC LIMIT 20000) m
                                WHERE text ILIKE ANY($1::text[]) ORDER BY ts_published DESC LIMIT 20`, [ukVariants(q).map((v) => "%" + like(v) + "%")]);
      return rows.map((r) => ({ id: "post:" + r.id, type: "post", label: (r.x || "").slice(0, 90), sub: `${r.source_id} · ${r.t}`, tier: 2 }));
    },
  };
  return S;
}

// ---- grouped search over every source ----
const PER_SOURCE_MS = 2500;
const soft = (p, ms = PER_SOURCE_MS) => Promise.race([Promise.resolve(p), new Promise((_, no) => setTimeout(() => no(new Error(`не встигло за ${ms / 1000} с`)), ms))]);
export const GROUPS = [["graph", "Граф"], ["registry", "Реєстри"], ["wanted", "Розшук"], ["courts", "Суди"], ["mentions", "Згадки в постах"], ["places", "Місця"], ["channels", "Канали"]];
// scope -> groups that run (detected case numbers / codes narrow «Усе» the same way)
const SCOPE = {
  all: ["graph", "registry", "wanted", "courts", "mentions", "places", "channels"],
  person: ["graph", "registry", "wanted", "courts", "mentions"], org: ["graph", "registry", "courts", "mentions"], fop: ["graph", "registry", "mentions"],
  code: ["graph", "registry", "courts", "mentions"], case: ["courts", "graph", "mentions"], place: ["places", "registry", "mentions"],
  channel: ["channels", "mentions"], post: ["mentions"], erdr: ["graph", "courts", "mentions"],
};
const ADDRESS = /(^|\s)(вул\.?|вулиця|просп\.?|проспект|пров\.?|провулок|бульв\.?|бульвар|площа|пл\.|ул\.|улица|шосе|набережна)(\s|$)|\d+\s*(кв|оф)\.?\s*\d/i;
const rank = (a, b) => a.tier - b.tier || (b.sim || 0) - (a.sim || 0) || (b.n || 0) - (a.n || 0);
const dedupe = (list) => { const seen = new Set(); return list.filter((r) => r?.id && !seen.has(r.id) && seen.add(r.id)); };

// graph names (search_names), ЄДР records and worker cards already in Neon; Ukrainian spellings of Russian input too
// A person query of 2–3 words («Прізвище Імʼя [По батькові]», either order) matches a person name only when every word of
// the shorter of the two has the same stem in the other: «Олійник Володимир Миколайович» keeps «Олійник Володимир» (no
// patronymic: marked as a possible namesake) but drops «Сінельник Володимир Миколайович» (word similarity alone ranked
// first name + patronymic above the surname) and «Олійник Володимир Петрович» (another person).
const STEM = (w) => w.replace(/[’ʼ`']/g, "").slice(0, Math.max(4, Math.min(6, w.length - 1)));
const WORDS = (s) => norm(s).toLowerCase().replace(/\s+-\s+.*$/, "").split(/[\s,]+/).filter((w) => w.length >= 2);
export function personMatch(query, name) {
  const a = WORDS(query), b = WORDS(name);
  if (a.length < 2 || b.length < 2) return { ok: true };
  const [s, l] = a.length <= b.length ? [a, b] : [b, a];
  const ok = s.every((w) => l.some((x) => STEM(x) === STEM(w) || (w.length <= 3 && x.startsWith(w))));
  return { ok, partial: ok && b.length < a.length };
}

// a 17-digit ЄРДР number: the proceeding node itself first (erdr:<n>, built from decisions that cite it and ARMA), then the
// court cases whose decisions carry it (GIN indexes on court_decisions.erdr / arma_assets.erdr, migrations 024–025)
const erdrSeen = new Map();
function erdrLookup(n) {
  if (erdrSeen.size > 200) erdrSeen.clear();
  if (!erdrSeen.has(n)) erdrSeen.set(n, Promise.all([
    query(`SELECT cause_num, count(*)::int n, min(adjudication_date)::text d0, max(adjudication_date)::text d1 FROM court_decisions WHERE erdr @> ARRAY[$1]::text[] GROUP BY 1 ORDER BY 2 DESC LIMIT 12`, [n]).catch(() => []),
    query(`SELECT count(*)::int n FROM arma_assets WHERE erdr @> ARRAY[$1]::text[]`, [n]).then((r) => r[0]?.n || 0).catch(() => 0),
  ]).then(([cases, arma]) => ({ cases, arma })).catch((e) => { erdrSeen.delete(n); throw e; }));
  return erdrSeen.get(n);
}
async function erdrItems(q) {
  const n = norm(q), g = peekGraph(), have = g?.byId?.get("erdr:" + n), { cases, arma } = await erdrLookup(n);
  if (!have && !cases.length && !arma) return [];
  const dec = cases.reduce((a, c) => a + c.n, 0);
  return [{ id: "erdr:" + n, type: "case", label: "Провадження ЄРДР " + n, tier: 0, exact: true, in_graph: !!have || undefined,
    sub: [dec ? `${dec} рішень у ${cases.length} справах` : "", arma ? `активів в АРМА: ${arma}` : "", have ? `зв'язків у графі: ${have.deg}` : ""].filter(Boolean).join(" · ") }];
}

async function graphGroup(q, kind, C) {
  if (kind === "erdr") return { items: await erdrItems(q) };
  const S = (v) => sources(v, C);
  const variants = /^(all|person|org|fop)$/.test(kind) ? ukVariants(q) : [q];
  const jobs = [];
  for (const v of variants) {
    const s = S(v);
    if (kind === "all") jobs.push(s.names(["person", "org", "case"]), s.edr(), s.reg(["edr_fop", "yc_fop", "yc_org"]));
    else if (kind === "person") jobs.push(s.names(["person"]));
    else if (kind === "org") jobs.push(s.names(["org"]), s.edr(), s.reg(["yc_org"]));
    else if (kind === "fop") jobs.push(s.reg(["edr_fop", "yc_fop"]));
    else if (kind === "case" && !/^\d/.test(v)) jobs.push(s.names(["case"]));
    else if (kind === "code" && /^\d{8}$|^\d{10}$/.test(v)) jobs.push(s.code());
  }
  const lists = await Promise.all(jobs.map((p) => p.catch(() => [])));
  const multi = WORDS(q).length >= 2 && /^(all|person)$/.test(kind);
  const items = lists.flat().flatMap((r) => {
    if (!multi || r.type !== "person") return [r];
    const m = personMatch(q, r.label);
    if (!m.ok) return [];
    return [m.partial ? { ...r, tier: Math.max(r.tier ?? 2, 2), sub: [r.sub, "без по батькові — можливо, тезка"].filter(Boolean).join(" · ") } : r];
  });
  return { items: dedupe(items.sort(rank)).map((r) => ({ ...r, in_graph: true })) };
}

// the lake index: names (legal entities + ФОП), codes, addresses, a person's roles in companies. Not in Neon, not in the graph yet.
async function registryGroup(q, kind) {
  let m;
  try { m = await soft(getManifest(), 2000); } catch (e) {
    return { items: [], status: "unavailable", note: /404/.test(String(e.message)) ? "індекс реєстрів ще публікується (lake workflow); спробуйте пізніше" : "реєстри недоступні: " + e.message };
  }
  const digits = q.replace(/[\s-]/g, "");
  const g = peekGraph();
  const item = (r, extra = {}) => {
    const c = card(r, m);
    const person = c.id?.startsWith("person:");
    return { id: c.id, type: person ? "person" : "registry", label: c.name, lake: true, in_graph: !!g?.byId?.has(c.id),
      sub: [c.kind === "fop" ? "ФОП" : "", c.kind === "lawyer" ? c.full_name || "" : "",
        c.code_type && c.code ? (c.code_type === "cert" ? `свідоцтво № ${c.code}` : `${c.code_type === "edrpou" ? "ЄДРПОУ" : "РНОКПП"} ${c.code}`) : "",
        c.stan, c.locality || c.region, c.contracts ? `ProZorro: ${c.contracts.won}/${c.contracts.placed}` : ""].filter(Boolean).join(" · "),
      tier: 2, ...extra };
  };
  if (kind === "code" || /^\d{8}$|^\d{10}$/.test(digits)) {
    if (!/^\d{8}$|^\d{10}$/.test(digits)) return { items: [] };
    const [r, live] = await Promise.all([byCode(digits), digits.length === 10 ? soft(liveByTin(digits, g), 2600).catch(() => []) : []]);
    const items = dedupe([...live, ...r.rows.map((x) => item(x, { tier: 0, exact: true }))]);
    return { items, note: digits.length === 10 && !items.length
      ? "Жоден публічний реєстр не публікує РНОКПП приватної особи. Знаходимо лише ФОП: ProZorro, YouControl, Clarity Project, тексти судових рішень («Суди»). Ніде немає — у відкритих даних цієї особи немає." : undefined };
  }
  const jobs = [];
  const fopOnly = kind === "fop";
  // the query and, for Russian input, its first Ukrainian spelling
  const names = [...new Set([q, ...ukVariants(q, 2)])].slice(0, 2);
  if (kind !== "place") for (const v of names) jobs.push(byName(v, m, fopOnly).then((r) => (r.rows || [])
    .filter((x) => kind !== "org" || x.kind === "org").filter((x) => kind !== "person" || x.kind === "fop")
    .map((x) => item(x, { tier: x.key === coreKey(searchKey(v, m), m) ? 1 : 2 }))));
  if (kind === "place" || ADDRESS.test(q)) jobs.push(byAddress(q, m).then((r) => (r.rows || []).map((x) => item(x, { sub: [x.locality, x.address].filter(Boolean).join(", ") || undefined }))));
  // «Прізвище Імʼя [По батькові]» -> companies where that person is founder / beneficiary / signer
  if (/^(all|person)$/.test(kind) && q.split(" ").length >= 2) {
    const words = q.split(" ");
    for (const v of [q, [words.at(-1), ...words.slice(0, -1)].join(" ")]) jobs.push(rolesOf(v, m).then((rows) => rows.slice(0, 15).map((x) => ({
      id: "registry:" + x.code, type: "registry", label: x.org || "ЄДР " + x.code, lake: true, in_graph: !!g?.byId?.has("registry:" + x.code), tier: 2,
      sub: `${ROLE[x.role] || x.role}: ${x.name}${x.stan ? " · " + x.stan : ""}`, role_of: x.name }))));
  }
  const lists = await Promise.all(jobs.map((p) => soft(p, 2400).catch(() => [])));
  return { items: dedupe(lists.flat().sort(rank)) };
}
const ROLE = { founder: "засновник", beneficiary: "бенефіціар", signer: "підписант / керівник" };

// A 10-digit РНОКПП, live, while the user waits (no queue): YouControl's search redirects to the ФОП page when it has one
// for that code; Clarity Project has a page for ФОП that ProZorro published. Both answer from a cloud IP in < 1 s. A hit
// is written to registry_hits as the local worker would (so the graph and later searches have it); nothing is stored when
// nothing is found. A private person who is not a ФОП is in neither, by design of the public data.
const UA_HDR = { "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36", "accept-language": "uk" };
const cap = (name) => name.split(/\s+/).filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1).toLowerCase()).join(" ");
const keep = (row) => query(`INSERT INTO registry_hits(query, qkind, source, ref, title, snippet, url, payload) VALUES ($1, 'rnokpp', $2, $3, $4, $5, $6, $7)
  ON CONFLICT (query, source, ref) DO UPDATE SET title = EXCLUDED.title, snippet = EXCLUDED.snippet, url = EXCLUDED.url, payload = EXCLUDED.payload`, row).catch(() => {});
async function liveByTin(tin, g) {
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 2300);
  const grab = async (url) => { const r = await fetch(url, { headers: UA_HDR, redirect: "follow", signal: ctl.signal }); return { url: r.url, ok: r.ok, html: r.ok ? (await r.text()).slice(0, 200000) : "" }; };
  const [yc, cl] = await Promise.all([grab("https://youcontrol.com.ua/search/?q=" + tin).catch(() => null), grab("https://clarity-project.info/edr/" + tin).catch(() => null)]);
  clearTimeout(t);
  const out = [];
  const yid = yc?.url.match(/fop_details\/(\d+)/)?.[1];
  if (yid) {
    const title = yc.html.match(/<title>(.*?)<\/title>/s)?.[1] || "";
    const m = title.match(/ФОП\s+([^,—|]+?)\s*(?:,\s*([^—|]+?))?\s*[—|]/);
    const name = cap((m?.[1] || "").trim()), city = (m?.[2] || "").trim();
    const kved = yc.html.replace(/<[^>]+>/g, "\n").match(/Основний:\s*\n\s*(\d{2}\.\d{2} [^\n]+)/)?.[1]?.trim();
    if (name) {
      const url = `https://youcontrol.com.ua/catalog/fop_details/${yid}/`, payload = { yc_id: yid, name, tin, city, kved_main: kved || null, url };
      out.push({ id: "fopx:" + yid, type: "registry", label: `ФОП ${name}${kved ? " · КВЕД " + kved : ""}`, sub: `ІПН ${tin} · ${city || "YouControl"}`, tier: 0, exact: true, in_graph: !!g?.byId?.has("fopx:" + yid) });
      keep([tin, "yc_fop", yid, `ФОП ${name} · КВЕД ${kved || "?"}`, city, url, JSON.stringify(payload)]);
    }
  }
  const ct = cl?.ok ? (cl.html.match(/<title>(.*?)<\/title>/s)?.[1] || "") : "";
  const cm = ct.match(new RegExp("^\\s*(.+?)\\s*\\(ЄДРПОУ\\s*" + tin + "\\)"));
  if (cm) {
    const name = cap(cm[1].replace(/^ФОП\s+/i, "")), url = "https://clarity-project.info/edr/" + tin;
    out.push({ id: "person:" + norm(name).toLowerCase(), type: "person", label: name, sub: `ІПН ${tin} · ФОП у ProZorro (Clarity Project)`, tier: 0, exact: true, in_graph: !!g?.byId?.has("person:" + norm(name).toLowerCase()) });
    keep([tin, "clarity_fop", tin, `ФОП ${name}`, "ФОП-постачальник у ProZorro", url, JSON.stringify({ name, tin, url })]);
  }
  return out;
}

// case numbers (exact / prefix), judges by name, decision texts (trigram index) that contain the words
async function courtsGroup(q, kind, C) {
  if (kind === "erdr") return { items: (await erdrLookup(norm(q))).cases.map((r) => ({ id: "case:" + r.cause_num.toLowerCase(), type: "case", label: "Справа " + r.cause_num,
    cause_num: r.cause_num, n: r.n, sub: `${r.n} рішень · ${r.d0 || "?"}…${r.d1 || "?"} · провадження ${norm(q)}`, tier: 1 })) };
  const isCase = /^\d{1,4}\/\d*/.test(q);
  const [cases, judges, texts] = await Promise.all([
    isCase ? sources(q, C).cases().catch(() => []) : [],
    isCase ? [] : soft(findJudges(q), 2400).catch(() => []),
    isCase ? { items: [] } : soft(decisionTexts(q), 2400).catch(() => ({ items: [], total: 0 })),
  ]);
  const t = texts.items.map((d) => ({ id: "decision:" + d.doc_id, type: "decision", label: `${d.f || "Рішення"} ${d.d || ""} · справа ${d.cause_num || "?"}`,
    sub: [d.court, d.judge].filter(Boolean).join(" · "), snippet: d.snippet, cause_num: d.cause_num, tier: 2 }));
  return { items: dedupe([...cases.map((c) => ({ ...c, exact: c.tier === 0 })), ...judges, ...t]), texts_total: texts.total || 0 };
}

// police wanted list (migration 021): surname / full-name prefix in Ukrainian or Russian spelling; «ст. 408» -> the article
const ARTICLE = /^(?:ст\.?|стаття|статья|article)\s*(\d{2,3}(?:-\d)?)\s*(?:кк)?$/i;
async function wantedGroup(q) {
  const art = q.match(ARTICLE);
  if (art) {
    const [r] = await query(`SELECT count(*) FILTER (WHERE removed IS NULL)::int n FROM wanted_persons WHERE article = $1`, [art[1]]);
    return { items: [{ id: "article:" + art[1], type: "article", label: `ст. ${art[1]} КК`, sub: `у розшуку: ${r?.n || 0} · статистика ОГП у картці`, tier: 0, exact: true }] };
  }
  const k = norm(q).toLowerCase().replace(/[’ʼ`]/g, "'");
  if (k.length < 4 || /\d/.test(k)) return { items: [] };
  const v = [...new Set([k, ...ukVariants(k, 2).map((x) => x.toLowerCase())])].slice(0, 3);
  const rows = await query(`SELECT id, name_u, last_name_u, first_name_u, middle_name_u, birth_date::text b, article_crim, category, agency, lost_date::text d, removed::text removed
      FROM wanted_persons WHERE name_u LIKE ANY($1) OR name_r LIKE ANY($1) ORDER BY removed NULLS FIRST, (name_u = ANY($2)) DESC, lost_date DESC NULLS LAST LIMIT 20`,
    [v.map((x) => x.replace(/[%_]/g, "") + "%"), v]);
  const AG = { police: "Нацполіція", sbu: "СБУ", dbr: "ДБР", nabu: "НАБУ", border_guard: "ДПСУ" };
  return { items: rows.map((r) => ({ id: "wanted:" + r.id, type: "wanted", label: [r.last_name_u, r.first_name_u, r.middle_name_u].filter(Boolean).join(" "),
    sub: [r.removed ? "знято з розшуку " + r.removed : "у розшуку", r.article_crim, AG[r.agency] || r.agency, r.b ? "нар. " + r.b : ""].filter(Boolean).join(" · "),
    tier: v.includes(r.name_u) ? 1 : 2, person: "person:" + r.name_u })),
    note: rows.length ? "Збіг за ПІБ не означає, що це та сама людина: звіряйте дату народження." : undefined };
}

async function placesGroup(q, C) {
  const [gz, ev] = await Promise.all([soft(findPlaces(q, { limit: 12, wait: 1800 }), 2400).catch(() => []), sources(q, C).place().catch(() => [])]);
  return { items: dedupe([...gz, ...ev.map((r) => ({ ...r, tier: Math.max(1, r.tier) }))].sort((a, b) => a.tier - b.tier)) };
}

async function mentionsGroup(q) {
  const [posts, web] = await Promise.all([postMentions(q, { limit: 5, ms: 2400 }), soft(webFound(q), 2000).catch(() => ({ items: [] }))]);
  // several words: only posts that carry all of them (as the graph's expand does); the first word alone is a namesake flood
  if ((posts.words || []).length > 1) { posts.items = (posts.items || []).filter((p) => p.all_words); posts.total = posts.all || 0; posts.capped = posts.capped && posts.total >= 1000; }
  const topic = posts.total ? { id: "topic:" + norm(q).toLowerCase(), type: "topic", label: norm(q), sub: `згадок у постах: ${posts.total}${posts.capped ? "+" : ""}`, tier: 3, n: posts.total } : null;
  return { items: posts.items.map((p) => ({ ...p, type: "post", label: p.snippet, sub: `${p.src} · ${p.date}`, tier: 3 })), count: posts.total, capped: posts.capped,
    words: posts.words, topic, web: web.items, note: posts.skipped };
}

export async function searchAll(q, kind = "all", part = "", src = null) {
  const detected = detect(q);
  const scope = kind === "all" && detected !== "all" ? detected : kind;
  if (scope === "url") return { q, kind, detected, groups: [], results: [] };
  // src: the bases ticked in the page's «Бази» filter (all when absent)
  const want = SCOPE[scope].filter((g) => (!src || src.includes(g)) && (part === "main" ? g !== "mentions" : part === "mentions" ? g === "mentions" : true));
  const C = await soft(getCaps(), 2000).catch(() => ({ names: false, trgm: false }));
  const t0 = Date.now();
  const run = {
    graph: () => graphGroup(q, scope, C), registry: () => registryGroup(q, scope), courts: () => courtsGroup(q, scope, C),
    mentions: () => mentionsGroup(q), places: () => placesGroup(q, C), wanted: () => wantedGroup(q), channels: () => sources(q, C).channel().then((items) => ({ items })),
  };
  // results opened before for this query (search_picks, migration 034) go first: the search learns from what was chosen
  const picksP = query(`SELECT id, n FROM search_picks WHERE q = $1`, [qkey(q)]).catch(() => []);
  const groups = await Promise.all(want.map(async (key) => {
    const s = Date.now();
    try {
      const r = await soft(run[key](), key === "mentions" ? 3000 : PER_SOURCE_MS + 300);
      const all = (r.items || []).filter((x) => !isPlaceholder(x.label));
      return { key, label: GROUPS.find((g) => g[0] === key)[1], status: r.status || "ok", count: r.count ?? all.length, items: all.slice(0, 20), ms: Date.now() - s,
        ...(r.note ? { note: r.note } : {}), ...(r.topic !== undefined ? { topic: r.topic, web: r.web, words: r.words, capped: r.capped } : {}), ...(r.texts_total ? { texts_total: r.texts_total } : {}) };
    } catch (e) {
      return { key, label: GROUPS.find((g) => g[0] === key)[1], status: /не встигло/.test(e.message) ? "timeout" : "error", note: String(e.message || e), count: 0, items: [], ms: Date.now() - s };
    }
  }));
  const picks = new Map((await soft(picksP, 800).catch(() => [])).map((r) => [r.id, r.n]));
  if (picks.size) for (const g of groups) {
    g.items.forEach((x) => { if (picks.has(x.id)) x.picked = picks.get(x.id); });
    g.items.sort((a, b) => (b.picked || 0) - (a.picked || 0));
  }
  // flat list: results picked before, exact identifiers, then by group priority
  const PRI = { graph: 1, registry: 2, wanted: 2, courts: 3, places: 4, channels: 5, mentions: 6 };
  const flat = [];
  for (const g of groups) g.items.forEach((x, i) => flat.push({ ...x, group: g.key, _p: x.picked ? -1 : x.exact || (x.tier === 0 && g.key !== "mentions") ? 0 : PRI[g.key], _i: i }));
  for (const g of groups) if (g.topic) flat.push({ ...g.topic, group: "mentions", _p: PRI.mentions, _i: -1 });
  const results = dedupe(flat.filter((x) => x.type !== "post").sort((a, b) => a._p - b._p || a._i - b._i)).slice(0, 20)
    .map(({ _p, _i, ...x }) => ({ ...x, strong: x.tier === 0 }));
  return { q, kind, scope, detected, part: part || undefined, groups, results, took_ms: Date.now() - t0 };
}

export default async function handler(req, res) {
  const url = new URL(req.url, "http://x");
  const q = norm(url.searchParams.get("q"));
  const kind = url.searchParams.get("kind") || "all";
  const part = url.searchParams.get("part") || "";
  if (!KINDS.includes(kind)) return json(res, 400, { error: "unknown kind" });
  if (!["", "main", "mentions"].includes(part)) return json(res, 400, { error: "part must be main|mentions" });
  if (q.length < 2) return json(res, 400, { error: "q too short" });
  const srcRaw = url.searchParams.get("src");
  const src = srcRaw == null ? null : srcRaw.split(",").filter(Boolean);
  if (src && src.some((g) => !GROUPS.some(([k]) => k === g))) return json(res, 400, { error: "unknown src" });
  try {
    const out = await searchAll(q, kind, part, src);
    if (part !== "mentions") out.queue = (await soft(query(`SELECT kind, status, created_at FROM query_queue WHERE query = $1 ORDER BY created_at DESC LIMIT 1`, [q]), 1500).catch(() => []))[0] || null;
    return json(res, 200, out);
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}

export function plan(S, kind, q) {
  return {
    all: () => [S.names(["person", "org", "case"]), S.edr(), S.reg(["edr_fop", "yc_fop", "yc_org"]), S.place(), S.channel()],
    person: () => [S.names(["person"])],
    org: () => [S.names(["org"]), S.edr(), S.reg(["yc_org"])],
    fop: () => [S.reg(["edr_fop", "yc_fop"])],
    code: () => (/^\d{8}$|^\d{10}$/.test(q) ? [S.code()] : []),
    case: () => [S.cases(), ...(/^\d/.test(q) ? [] : [S.names(["case"])])],
    place: () => [S.place()],
    channel: () => [S.channel()],
    post: () => [S.post()],
  }[kind]();
}
