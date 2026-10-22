import { json, query } from "./_db.js";

// GET /api/crime?keys=total,sec:military,art:336&measure=registered&months=36
// GET /api/crime?article=408                  -> same as keys=art:408
// GET /api/crime?catalog=1                    -> every key with its published label and the periods it covers
// Office of the Prosecutor General report forms (src/ogp_reports.py, migration 022, docs/ogp_reports.md). National figures
// only: the open files have no region breakdown, so ?region= is accepted and ignored (the response says so).
// series: month increments from ogp_monthly (months > 1 where a monthly report is missing; negative = a revision);
// ytd: the latest cumulative value per key next to the same months of the previous year.
// sec:military since 2022 is derived (total − published sections): the section is not published under martial law.
const DEFAULT_KEYS = ["total", "sec:military", "sec:national_security", "sec:peace_war", "art:111", "art:111-1", "art:336",
  "art:368", "art:369", "art:369-2", "art:191", "detected_by:nabu"];
const MEASURES = ["registered", "suspicion", "persons_suspected", "persons_identified", "investigated", "started", "value"];
const KEY_RE = /^(total|sections_published|(sec|art|detected_by|money|oz):[\w:.\-Ѐ-ӿ ’'/,()]{1,120})$/;

export default async function handler(req, res) {
  const u = new URL(req.url, "http://x");
  const form = ["forma_1", "forma_2", "forma_1_oz", "forma_1_lft"].includes(u.searchParams.get("form")) ? u.searchParams.get("form") : "forma_1";
  const measure = MEASURES.includes(u.searchParams.get("measure")) ? u.searchParams.get("measure") : form === "forma_2" ? "persons_suspected" : "registered";
  const months = Math.min(120, Math.max(3, +(u.searchParams.get("months") || 36)));
  const art = (u.searchParams.get("article") || "").replace(/^ст\.?\s*/i, "").trim();
  let keys = art ? [`art:${art}`] : (u.searchParams.get("keys") || "").split(",").map((k) => k.trim()).filter(Boolean);
  keys = keys.filter((k) => KEY_RE.test(k)).slice(0, 20);
  if (!keys.length) keys = DEFAULT_KEYS;
  const note = u.searchParams.get("region") ? "Звіти ОГП у відкритих даних — лише по Україні загалом; регіону немає." : undefined;
  try {
    if (u.searchParams.get("catalog")) {
      const catalog = await query(`SELECT k.form, k.key, k.label, min(s.period_to)::text first, max(s.period_to)::text last,
          array_agg(DISTINCT s.measure) measures FROM ogp_keys k JOIN ogp_stats s USING (form, key) GROUP BY 1, 2, 3 ORDER BY 1, 2`, []);
      return json(res, 200, { catalog });
    }
    const [series, ytd, labels] = await Promise.all([
      query(`SELECT key, month::date::text month, value, months, derived FROM ogp_monthly
             WHERE form = $1 AND measure = $2 AND key = ANY($3) AND month >= date_trunc('month', now()) - ($4::int * interval '1 month')
             ORDER BY key, month`, [form, measure, keys, months]),
      // latest cumulative value per key and the same period a year earlier (year-on-year)
      query(`WITH last AS (SELECT DISTINCT ON (key) key, period_to, value, derived FROM ogp_ytd
                           WHERE form = $1 AND measure = $2 AND key = ANY($3) ORDER BY key, period_to DESC)
             SELECT l.key, l.period_to::text period_to, l.value, l.derived, p.value prev
             FROM last l LEFT JOIN ogp_ytd p ON p.form = $1 AND p.measure = $2 AND p.key = l.key AND p.period_to = l.period_to - interval '1 year'`,
        [form, measure, keys]),
      query(`SELECT key, label FROM ogp_keys WHERE form = $1 AND key = ANY($2)`, [form, keys]),
    ]);
    const L = Object.fromEntries(labels.map((r) => [r.key, r.label]));
    if (keys.includes("sec:military")) L["sec:military"] = "Військові злочини (розділ XIX КК, ст. 402–435); з 2022 — розрахунково: усього мінус опубліковані розділи";
    return json(res, 200, { form, measure, months, note, keys: keys.map((k) => ({ key: k, label: L[k] || null })), series, ytd });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
