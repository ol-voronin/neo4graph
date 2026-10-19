import { sql, json } from "./_db.js";

// GET /api/predictions -> prospective forecasts (src/nowcast.py): every row was written before its outcome window opened.
// Research output, not an air-raid warning.
export default async function handler(req, res) {
  try {
    const rows = await sql`SELECT model, target_date::text AS d, p, outcome, issued_at, n_train FROM predictions
                           WHERE target_date >= current_date - 180 ORDER BY target_date DESC, model`.catch((e) =>
      /does not exist/.test(String(e.message)) ? [] : Promise.reject(e));
    return json(res, 200, { rows });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
