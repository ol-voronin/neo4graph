import { json, query } from "./_db.js";

// GET /api/wanted                 -> analytics: active records by article group / agency / region, monthly flow of new
//                                    records (by the date the person went into hiding), daily added/removed, missing persons
// GET /api/wanted?name=Прізвище Імʼя -> records whose Ukrainian or Russian full name starts with the query (≤ 30)
// Source: Національна поліція open data on data.gov.ua (src/police_wanted.py, migration 021). The list is published by the
// police for public search; the project shows what is published (no phone numbers, no photos). A name match is not an
// identification: namesakes are common, the birth date is shown to tell them apart.
export const GROUPS = ["military_evasion", "war_collab", "corruption", "border", "violent", "property", "other", "no_article"];

export async function byName(name, limit = 30) {
  const q = (name || "").toLowerCase().replace(/[’ʼ`]/g, "'").replace(/\s+/g, " ").trim();
  if (q.split(" ").length < 2 || q.length < 5) return [];
  return query(`SELECT id, last_name_u, first_name_u, middle_name_u, birth_date::text birth_date, category, article_crim, art_group, restraint,
      ovd, agency, region, lost_date::text lost_date, first_seen::text first_seen, removed::text removed
    FROM wanted_persons WHERE name_u LIKE $1 OR name_r LIKE $1 ORDER BY removed NULLS FIRST, lost_date DESC NULLS LAST LIMIT $2`, [q + "%", limit]);
}

export default async function handler(req, res) {
  const u = new URL(req.url, "http://x");
  const name = u.searchParams.get("name");
  try {
    if (name != null) return json(res, 200, { name, items: await byName(name), note: "Збіг за ПІБ не означає, що це та сама людина: перевіряйте дату народження." });
    const months = Math.min(120, Math.max(6, +(u.searchParams.get("months") || 48)));
    const [groups, agencies, regions, flow, daily, missing, missingRegions, meta] = await Promise.all([
      query(`SELECT art_group, count(*)::int active FROM wanted_persons WHERE removed IS NULL GROUP BY 1 ORDER BY 2 DESC`),
      query(`SELECT agency, art_group, count(*)::int active FROM wanted_persons WHERE removed IS NULL GROUP BY 1, 2`),
      query(`SELECT region, art_group, count(*)::int active FROM wanted_persons WHERE removed IS NULL GROUP BY 1, 2`),
      query(`SELECT month::text AS month, art_group, sum(n)::int n, sum(still_wanted)::int still FROM wanted_monthly
             WHERE month >= date_trunc('month', now()) - ($1::int * interval '1 month') GROUP BY 1, 2 ORDER BY 1`, [months]),
      query(`SELECT date::text AS date, art_group, sum(active)::int active, sum(added)::int added, sum(removed)::int removed FROM wanted_daily
             WHERE date >= current_date - 120 GROUP BY 1, 2 ORDER BY 1`),
      query(`SELECT month::text AS month, sex, age_band, sum(n)::int n FROM missing_monthly WHERE snapshot = (SELECT max(snapshot) FROM missing_monthly)
             AND month >= '2021-01-01' GROUP BY 1, 2, 3 ORDER BY 1`),
      query(`SELECT region, sum(n)::int n FROM missing_monthly WHERE snapshot = (SELECT max(snapshot) FROM missing_monthly) AND month >= '2022-02-01' GROUP BY 1 ORDER BY 2 DESC`),
      query(`SELECT count(*)::int total, count(*) FILTER (WHERE removed IS NULL)::int active, min(first_seen)::text since, max(last_seen)::text updated,
             (SELECT max(snapshot)::text FROM missing_monthly) missing_snapshot FROM wanted_persons`),
    ]);
    return json(res, 200, { meta: meta[0], groups, agencies, regions, flow, daily, missing, missing_regions: missingRegions });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
