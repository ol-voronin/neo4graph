import { neon } from "@neondatabase/serverless";

export const sql = neon(process.env.DATABASE_URL);

export function json(res, status, body) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.end(JSON.stringify(body));
}

export const CAUSE_RE = /^\d{1,4}\/\d{1,6}\/\d{2}(?:-[\w/]+)?$/;
export const EDRPOU_RE = /^\d{8}$/;

export function classify(q) {
  q = q.trim();
  if (/^https?:\/\//i.test(q)) return "url";
  if (CAUSE_RE.test(q)) return "case";
  if (EDRPOU_RE.test(q)) return "edrpou";
  if (/^(тов|пат|прат|ат|тдв|дп|кп|пп|фг|го|бф|ооо)\b|[«»"]/i.test(q)) return "org";
  if (/^[А-ЯІЇЄҐA-Z][\w'’-]+(\s+[А-ЯІЇЄҐA-Z][\w'’-]+){1,2}$/u.test(q)) return "person";
  return "text";
}
