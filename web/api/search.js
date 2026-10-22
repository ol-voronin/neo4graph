import { query, json, CAUSE_RE } from "./_db.js";
import { norm, caseKey, isPlaceholder } from "./_graph.js";
import { ukVariants } from "./_text.js";

// GET /api/search?q=...&kind=all|person|org|fop|code|case|place|channel|post
// Ranked results (exact code / case number / full name -> name prefix -> trigram similarity), at most 20, with ids of /api/graph nodes.
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
        query(`SELECT DISTINCT ON (source, ref) source, ref, title FROM registry_hits WHERE source IN ('edr_fop', 'yc_fop') AND (payload::text LIKE $1 OR snippet LIKE $1) LIMIT 10`, [pat]).catch(() => []),
        query(`SELECT edrpou, short_name, name FROM edr_orgs WHERE (founders || signers || beneficiaries)::text LIKE $1 LIMIT 10`, [pat]),
      ]);
      return [...fop.map((r) => ({ id: REG[r.source] + r.ref, type: "registry", label: r.title, sub: "ІПН " + t, tier: 0 })),
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

export default async function handler(req, res) {
  const url = new URL(req.url, "http://x");
  const q = norm(url.searchParams.get("q"));
  let kind = url.searchParams.get("kind") || "all";
  if (!KINDS.includes(kind)) return json(res, 400, { error: "unknown kind" });
  if (q.length < 2) return json(res, 400, { error: "q too short" });
  const detected = detect(q);
  if (kind === "all" && detected !== "all") kind = detected;
  try {
    if (kind === "url") return json(res, 200, { q, kind, detected, results: [] });
    const C = await getCaps().catch(() => ({ names: false, trgm: false }));
    // name lookups also run for Ukrainian spellings of Russian input (Миндич -> Міндіч), at most 4 spellings
    const run = (/^(all|person|org|fop|place|channel)$/.test(kind) ? ukVariants(q) : [q]).flatMap((v) => plan(sources(v, C), kind, v));
    const warn = [];
    const lists = await Promise.all(run.map((p) => p.catch((e) => (warn.push(String(e.message || e)), []))));
    const seen = new Set(), results = [];
    for (const r of lists.flat().sort((a, b) => a.tier - b.tier || (b.sim || 0) - (a.sim || 0) || (b.n || 0) - (a.n || 0))) {
      if (seen.has(r.id)) continue;
      seen.add(r.id); results.push({ ...r, strong: r.tier === 0 });
      if (results.length >= 20) break;
    }
    const queue = (await query(`SELECT status, created_at FROM query_queue WHERE query = $1 ORDER BY created_at DESC LIMIT 1`, [q]).catch(() => []))[0] || null;
    return json(res, 200, { q, kind, detected, results, queue, ...(warn.length ? { warn } : {}) });
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
