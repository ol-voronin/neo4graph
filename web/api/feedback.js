import { sql, json } from "./_db.js";

// What the researcher teaches the graph and the search (migration 034).
// GET  /api/feedback                               -> {links: [{a, b, rel, verdict, note, at}]}
// POST /api/feedback {a, b, rel, verdict, note?}   -> verdict 'yes' | 'no' on one edge; verdict null removes it
// POST /api/feedback {pick: {q, id, label?}}       -> a search result opened for q (search ranks it first next time)
export const qkey = (q) => String(q || "").normalize("NFC").toLowerCase().replace(/[’ʼ`]/g, "'").replace(/\s+/g, " ").trim();

export default async function handler(req, res) {
  try {
    if (req.method === "POST") {
      let body = "";
      for await (const chunk of req) body += chunk;
      const r = JSON.parse(body || "{}");
      if (r.pick) {
        const q = qkey(r.pick.q), id = String(r.pick.id || "");
        if (q.length < 2 || q.length > 300 || !id || id.length > 300) return json(res, 400, { error: "pick needs q and id" });
        await sql`INSERT INTO search_picks(q, id, label) VALUES (${q}, ${id}, ${String(r.pick.label || "").slice(0, 300)})
                  ON CONFLICT (q, id) DO UPDATE SET n = search_picks.n + 1, last_at = now(), label = EXCLUDED.label`;
        return json(res, 200, { ok: true });
      }
      const { a, b, rel, verdict, note } = r;
      if (![a, b, rel].every((x) => typeof x === "string" && x && x.length <= 400)) return json(res, 400, { error: "a, b, rel required" });
      if (verdict == null) {
        await sql`DELETE FROM link_feedback WHERE a = ${a} AND b = ${b} AND rel = ${rel}`;
        return json(res, 200, { ok: true, removed: true });
      }
      if (!["yes", "no"].includes(verdict)) return json(res, 400, { error: "verdict must be yes | no | null" });
      await sql`INSERT INTO link_feedback(a, b, rel, verdict, note) VALUES (${a}, ${b}, ${rel}, ${verdict}, ${note ? String(note).slice(0, 500) : null})
                ON CONFLICT (a, b, rel) DO UPDATE SET verdict = EXCLUDED.verdict, note = EXCLUDED.note, created_at = now()`;
      return json(res, 200, { ok: true });
    }
    const links = await sql`SELECT a, b, rel, verdict, note, created_at::date::text at FROM link_feedback ORDER BY created_at DESC LIMIT 5000`;
    return json(res, 200, { links });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
