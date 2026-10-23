// One rule for links that rest on a person's name alone, i.e. no ЄДРПОУ, РНОКПП, case number, ЄРДР number or record id
// joins the two objects, only the name does (wanted list by ПІБ, ЄДР founders / signers / beneficiaries by name, NAZK
// declarations, ФОП, debtors, court summons, the court register's full-text search, lawyers named in decision texts):
//   exactly one candidate in that source  -> the link is kept and marked probable: { probable: true } («можливо той самий»)
//   several candidates                    -> no link is asserted: a "namesake" edge with the count (cands), the user
//                                            tells them apart by patronymic / birth date
// A candidate is a distinct identity as far as the source can tell: the full name, plus the birth date or year when the
// source publishes one (the wanted list, the debtors' register). Links by exact identifiers stay as they are (certain).
// The page draws probable edges dashed and tags them «можливо» (web/index.html, idTag); this module is the server side.
export const PROBABLE = "можливо той самий (єдиний збіг за ПІБ у джерелі)";

const low = (s) => String(s || "").toLowerCase().replace(/[’ʼ`]/g, "'").replace(/\s+/g, " ").trim();
// the identity a source can tell apart: name (+ birth date / year)
export const identityKey = (name, born) => low(name) + (born ? "|" + String(born).slice(0, 10) : "");

// candidates per group: count(rows, (r) => query, (r) => identityKey(...)) -> Map(group -> number of distinct identities)
export function countCandidates(rows, groupOf, keyOf) {
  const m = new Map();
  for (const r of rows) { const g = groupOf(r); if (g == null) continue; (m.get(g) || m.set(g, new Set()).get(g)).add(keyOf(r)); }
  return new Map([...m].map(([g, s]) => [g, s.size]));
}

// The edge to draw for a name-only link: { rel, attrs }. `rel` is what the link would be if the identity were known
// (wanted, declaration, fop_record, lawyer_in …); n is the number of candidates in the source (<= 1: unique).
// `what` names the candidates in the namesake detail («записи розшуку», «декларанти НАЗК»).
export function nameLink(rel, n, attrs = {}, what = "записів") {
  if (!(n > 1)) return { rel, attrs: { ...attrs, probable: true } };
  return { rel: "namesake", attrs: { ...attrs, cands: n, of: rel,
    detail: [`${what} з таким ПІБ у джерелі: ${n} — той самий чи інший, не встановлено`, attrs.detail].filter(Boolean).join(" · ") } };
}
