import { sql, json } from "./_db.js";

// GET /api/predictions -> prospective forecasts (src/nowcast.py): every row was written before its outcome window opened.
// Research output, not an air-raid warning. Each row carries `region`: an oblast slug for the pooled oblast model,
// 'kharkivska' for the Kharkiv models, 'ukraine' for the national ones (before migration 015 the column does not exist
// and the region is inferred the same way).
// Also, for the explanation on hover: each row's features (what the number was made of), the models' record so far by
// probability band (calibration: of the days given 80–100 %, how many had the event), and per-region label coverage
// (an oblast without complete daily summaries has few known days, so its probability leans on the all-oblast rate).
const missing = (e) => /does not exist/.test(String(e.message));

export default async function handler(req, res) {
  try {
    const rows = await sql`SELECT model, region_id AS region, target_date::text AS d, p, outcome, issued_at, n_train, features FROM predictions
                           WHERE target_date >= current_date - 180 ORDER BY target_date DESC, model, region_id`
      .catch(async (e) => {
        if (!missing(e)) throw e;
        const old = await sql`SELECT model, target_date::text AS d, p, outcome, issued_at, n_train FROM predictions
                              WHERE target_date >= current_date - 180 ORDER BY target_date DESC, model`
          .catch((e2) => (missing(e2) ? [] : Promise.reject(e2)));
        return old.map((r) => ({ ...r, region: r.model.startsWith("kh_") ? "kharkivska" : "ukraine" }));
      });
    const [calib, coverage] = await Promise.all([
      sql`SELECT model, least(4, floor(p * 5))::int AS bin, count(*)::int n, sum(outcome)::int hits FROM predictions
          WHERE outcome IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2`.catch(() => []),
      sql`SELECT region_id AS region, count(*)::int days, count(*) FILTER (WHERE complete)::int complete,
            count(*) FILTER (WHERE missiles IS NOT NULL OR drones IS NOT NULL)::int known,
            count(*) FILTER (WHERE coalesce(missiles, 0) >= 7 OR coalesce(drones, 0) >= 20)::int massive
          FROM oblast_daily_gt WHERE date >= current_date - 260 GROUP BY 1`.catch(() => []),
    ]);
    return json(res, 200, { rows, calib, coverage });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
