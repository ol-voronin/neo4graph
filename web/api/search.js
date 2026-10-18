import { sql, json, classify } from "./_db.js";

// GET /api/search?q=...  -> what the database already holds for the query, by kind
export default async function handler(req, res) {
  const url = new URL(req.url, "http://x");
  const q = (url.searchParams.get("q") || "").trim();
  if (q.length < 2) return json(res, 400, { error: "q too short" });
  const kind = classify(q);
  try {
    const out = { q, kind };
    if (kind === "case") {
      out.decisions = (await sql`SELECT count(*)::int n FROM court_decisions WHERE cause_num = ${q}`)[0].n;
    } else if (kind === "edrpou") {
      out.edr = await sql`SELECT edrpou, short_name, stan FROM edr_orgs WHERE edrpou = ${q}`;
    } else {
      const pat = "%" + q + "%";
      out.persons = await sql`SELECT name_norm, count(*)::int n FROM ee_persons WHERE name_norm ILIKE ${pat} GROUP BY 1 ORDER BY 2 DESC LIMIT 10`;
      out.orgs = await sql`SELECT name, max(edrpou) edrpou, count(*)::int n FROM ee_orgs WHERE name ILIKE ${pat} GROUP BY 1 ORDER BY 3 DESC LIMIT 10`;
      out.edr = await sql`SELECT edrpou, short_name, stan FROM edr_orgs WHERE name ILIKE ${pat} OR short_name ILIKE ${pat} OR founders::text ILIKE ${pat} OR signers::text ILIKE ${pat} LIMIT 10`;
      out.judges = await sql`SELECT judge, count(*)::int n FROM court_decisions WHERE judge ILIKE ${pat} GROUP BY 1 ORDER BY 2 DESC LIMIT 10`;
      out.posts = (await sql`SELECT count(*)::int n FROM messages WHERE text ILIKE ${pat}`)[0].n;
    }
    const queue = await sql`SELECT status, created_at FROM query_queue WHERE query = ${q} ORDER BY created_at DESC LIMIT 1`.catch(() => []);
    out.queue = queue[0] || null;
    return json(res, 200, out);
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
