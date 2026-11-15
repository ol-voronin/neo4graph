import pg from "pg";

const { Pool } = pg;

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false },
  max: 5,
  idleTimeoutMillis: 20000,
});

export async function query(text, params = []) {
  const { rows } = await pool.query(text, params);
  return rows;
}

export function sql(stringsOrText, ...values) {
  if (Array.isArray(stringsOrText) && Object.prototype.hasOwnProperty.call(stringsOrText, "raw")) {
    let text = stringsOrText[0];
    for (let i = 0; i < values.length; i++) {
      text += `$${i + 1}${stringsOrText[i + 1]}`;
    }
    return query(text, values);
  }
  return query(stringsOrText, values[0] || []);
}
sql.query = query;

export function json(res, status, body) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.end(JSON.stringify(body));
}

export const CAUSE_RE = /^\d{1,4}\/\d{1,6}\/\d{2}(?:-[\w/]+)?$/;
export const EDRPOU_RE = /^\d{8}$/;
export const RNOKPP_RE = /^\d{10}$/;

export function classify(q) {
  q = q.trim();
  if (/^https?:\/\//i.test(q)) return "url";
  if (CAUSE_RE.test(q)) return "case";
  if (EDRPOU_RE.test(q)) return "edrpou";
  if (RNOKPP_RE.test(q)) return "rnokpp";
  if (/^(тов|пат|прат|ат|тдв|дп|кп|пп|фг|го|бф|ооо)\b|[«»"]/i.test(q)) return "org";
  if (/^\p{Lu}[\p{L}'’-]+(\s+\p{Lu}[\p{L}'’.-]+){1,2}$/u.test(q)) return "person";
  return "text";
}
