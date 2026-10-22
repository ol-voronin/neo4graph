import { sql, json } from "./_db.js";

// GET /api/predictions -> prospective forecasts (src/nowcast.py): every row was written before its outcome window opened.
// Research output, not an air-raid warning. Each row carries `region`: an oblast slug for the pooled oblast model,
// 'kharkivska' for the Kharkiv models, 'ukraine' for the national ones (before migration 015 the column does not exist
// and the region is inferred the same way).
const missing = (e) => /does not exist/.test(String(e.message));

export default async function handler(req, res) {
  try {
    const rows = await sql`SELECT model, region_id AS region, target_date::text AS d, p, outcome, issued_at, n_train FROM predictions
                           WHERE target_date >= current_date - 180 ORDER BY target_date DESC, model, region_id`
      .catch(async (e) => {
        if (!missing(e)) throw e;
        const old = await sql`SELECT model, target_date::text AS d, p, outcome, issued_at, n_train FROM predictions
                              WHERE target_date >= current_date - 180 ORDER BY target_date DESC, model`
          .catch((e2) => (missing(e2) ? [] : Promise.reject(e2)));
        return old.map((r) => ({ ...r, region: r.model.startsWith("kh_") ? "kharkivska" : "ukraine" }));
      });
    return json(res, 200, { rows });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
