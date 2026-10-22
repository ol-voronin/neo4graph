import { getManifest, lookup, groupSpan, searchKey, coreKey } from "./_lake.js";

// GET /api/registry?q=<text>&kind=fop|code|name|address[&region=<область>]
// Open-registry lookup over the lake's search index (GitHub release Parquet, no Neon rows):
//   code    — ЄДРПОУ (8 digits: ЄДР, ProZorro) or РНОКПП (10 digits: only ФОП that ProZorro publishes as suppliers)
//   name    — name prefix across legal entities and ФОП (legal-form words like «ТОВ» are ignored)
//   fop     — the same, ФОП only (ЄДР ФОП dump + ProZorro suppliers)
//   address — locality / street words; addresses exist only for ProZorro participants (ЄДР dumps carry none)
// kind is optional: digits -> code, otherwise name. Up to 20 results, ranked; ids follow web/api/graph.js
// (registry:<ЄДРПОУ> for a ЄДР legal entity, fop:<record> for a ЄДР ФОП record, person:<full name> for a ФОП from ProZorro).
const LIMIT = 20;

// own helper instead of ./_db.js: this endpoint never touches Neon
function json(res, status, body) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Cache-Control", status === 200 ? "public, max-age=0, s-maxage=3600, stale-while-revalidate=86400" : "no-store");
  res.end(JSON.stringify(body));
}
const SRC = { edr: "ЄДР (data.gov.ua)", prozorro: "ProZorro", erb: "Єдиний реєстр боржників" };

function card(r, m) {
  const sources = (r.src || "").split(",").filter(Boolean).map((s) => SRC[s] || s);
  if (r.debts) sources.push(SRC.erb);
  return {
    id: r.id, kind: r.kind, name: r.name, full_name: r.full_name && r.full_name !== r.name ? r.full_name : undefined,
    code: r.code || undefined, code_type: r.code_type || undefined, stan: r.stan || undefined, since: r.since || undefined,
    region: r.region || undefined, locality: r.locality || undefined, address: r.address || undefined,
    contracts: r.n_won || r.n_bought ? { won: r.n_won, placed: r.n_bought } : undefined,
    debts: r.debts || undefined, sources, as_of: m.built.slice(0, 10),
  };
}

// higher is better: exact key, whole-word prefix, active registration, public contracts
function score(r, key, q) {
  let s = 0;
  if (key === q) s += 100; else if (key.startsWith(q + " ")) s += 50;
  if (r.stan === "зареєстровано") s += 20; else if (!r.stan) s += 10;
  s += Math.min(15, Math.log2(1 + (r.n_won || 0) + (r.n_bought || 0)) * 3);
  return s;
}

function rank(rows, scoreOf, filter = () => true) {
  const seen = new Set(), out = [];
  for (const r of rows.filter(filter).map((r) => ({ r, s: scoreOf(r) })).sort((a, b) => b.s - a.s)) {
    if (seen.has(r.r.id)) continue;
    seen.add(r.r.id); out.push(r.r);
    if (out.length >= LIMIT) break;
  }
  return out;
}

async function byCode(code) {
  const { rows } = await lookup("ids", code, { exact: true });
  return { rows: rank(rows, (r) => score(r, r.code, code)), truncated: false };
}

async function byName(q, m, fopOnly, regionOk) {
  const k = coreKey(searchKey(q, m), m);
  if (k.length < 3) return { rows: [], truncated: false, error: "too short" };
  const words = k.split(" ");
  // people are indexed surname-first; also try «Імʼя [По батькові] Прізвище» -> «Прізвище Імʼя [По батькові]»
  const variants = [k];
  if (words.length >= 2 && words.length <= 3) variants.push([words.at(-1), ...words.slice(0, -1)].join(" "));
  const res = await Promise.all(variants.map((v) => lookup("names", v)));
  const rows = res.flatMap((x, i) => x.rows.map((r) => ({ ...r, _q: variants[i] })));
  const filter = (r) => (!fopOnly || r.kind === "fop") && regionOk(r);
  return { rows: rank(rows, (r) => score(r, r.key, r._q), filter), truncated: res.some((x) => x.truncated) };
}

async function byAddress(q, m, regionOk) {
  const all = searchKey(q, m).split(" ").filter(Boolean);
  const words = all.filter((w) => w.length >= 3 && !/^\d+$/.test(w) && !m.stop.includes(w));
  if (!words.length) return { rows: [], truncated: false, error: "need a locality or street word (3+ letters)" };
  // look up the rarest word: the one whose prefix spans the fewest row groups
  const spec = m.files.addr;
  const width = (w) => { const [a, b] = groupSpan(spec, w); return b - a; };  // 0: absent word, empty answer at once
  const pivot = [...words].sort((a, b) => width(a) - width(b) || b.length - a.length)[0];
  const { rows, truncated } = await lookup("addr", pivot);
  const others = all.filter((w) => w !== pivot && !m.stop.includes(w));
  const filter = (r) => {
    const hay = " " + searchKey(`${r.locality || ""} ${r.address || ""}`, m) + " ";
    return regionOk(r) && others.every((w) => (/^\d+$/.test(w) ? hay.includes(" " + w + " ") : hay.includes(" " + w)));
  };
  return { rows: rank(rows, (r) => score(r, r.token, pivot), filter), truncated };
}

export default async function handler(req, res) {
  const url = new URL(req.url, "http://x");
  const q = (url.searchParams.get("q") || "").trim();
  let kind = (url.searchParams.get("kind") || "").trim();
  const region = (url.searchParams.get("region") || "").trim();
  if (q.length < 2) return json(res, 400, { error: "q too short" });
  const digits = q.replace(/[\s-]/g, "");
  if (!kind) kind = /^\d+$/.test(digits) ? "code" : "name";
  if (!["fop", "code", "name", "address"].includes(kind)) return json(res, 400, { error: "kind must be fop|code|name|address" });
  const t0 = Date.now();
  try {
    const m = await getManifest();
    const rk = region ? searchKey(region, m).split(" ")[0] : "";
    const regionOk = (r) => !rk || searchKey(r.region || "", m).includes(rk);
    let out;
    if (kind === "code") {
      if (!/^\d{8}$|^\d{10}$/.test(digits)) return json(res, 400, { error: "code must be 8 digits (ЄДРПОУ) or 10 digits (РНОКПП)" });
      out = await byCode(digits);
    } else if (kind === "address") {
      out = await byAddress(q, m, regionOk);
    } else {
      out = await byName(q, m, kind === "fop", regionOk);
    }
    if (out.error) return json(res, 400, { error: out.error });
    return json(res, 200, {
      q, kind, region: region || undefined, results: out.rows.map((r) => card(r, m)), truncated: out.truncated || undefined,
      as_of: m.built, took_ms: Date.now() - t0,
      note: kind === "code" && digits.length === 10
        ? "РНОКПП є персональними даними; показуємо лише якщо офіційне відкрите джерело (ProZorro) публікує його як ідентифікатор ФОП-постачальника."
        : undefined,
    });
  } catch (e) {
    return json(res, 502, { error: String(e.message || e) });
  }
}
