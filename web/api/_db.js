import { neon } from "@neondatabase/serverless";

export const sql = neon(process.env.DATABASE_URL);
// Parameterised query with a text string: driver 0.10.x takes sql(text, params); 1.x moved it to sql.query(text, params).
export const query = (text, params = []) => (typeof sql.query === "function" ? sql.query(text, params) : sql(text, params));

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
