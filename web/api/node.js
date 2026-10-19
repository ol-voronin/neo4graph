import { sql, json } from "./_db.js";

// GET /api/node?id=<graph node id>&q=<name to highlight>
// Details for the selected object, on demand: full registry records, decision text fragments, counts across sources,
// and a dated timeline, so the side panel can say what the thing is and where it came from.
const sentencesWith = (text, needles, max = 6) => {
  if (!text) return [];
  const out = [];
  const parts = text.replace(/\s+/g, " ").split(/(?<=[.;!?])\s+/);
  const re = needles.filter(Boolean).map((n) => new RegExp(n.split(/\s+/)[0].replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"));
  for (const s of parts) {
    if (re.some((r) => r.test(s))) out.push(s.trim().slice(0, 400));
    if (out.length >= max) break;
  }
  return out;
};

export default async function handler(req, res) {
  const url = new URL(req.url, "http://x");
  const id = url.searchParams.get("id") || "";
  const q = url.searchParams.get("q") || "";
  const i = id.indexOf(":");
  const kind = id.slice(0, i), key = id.slice(i + 1);
  try {
    if (kind === "decision") {
      const r = (await sql`SELECT doc_id, cause_num, court, judgment_form, justice_kind, category, adjudication_date, receipt_date, date_publ, judge, doc_url, status, year,
                           length(text) AS text_len, left(text, 1200) AS head, text FROM court_decisions WHERE doc_id = ${key}`)[0];
      if (!r) return json(res, 404, { error: "no such decision" });
      const hits = await sql`SELECT query, snippet FROM registry_hits WHERE source = 'reyestr' AND ref = ${key}`;
      const needles = [q, ...hits.map((h) => h.query)];
      const frag = sentencesWith(r.text, needles);
      const siblings = await sql`SELECT count(*)::int n, min(adjudication_date) d0, max(adjudication_date) d1 FROM court_decisions WHERE cause_num = ${r.cause_num}`;
      delete r.text;
      return json(res, 200, { kind, record: r, found_by: hits, fragments: frag, case_summary: siblings[0],
        explain: `${r.judgment_form || "Рішення"} від ${r.adjudication_date || "?"}, ${r.court || ""}${r.justice_kind ? " · " + r.justice_kind.toLowerCase() + " судочинство" : ""}${r.cause_num ? " · справа " + r.cause_num : ""}.` +
          (hits.length ? ` У граф потрапило через пошук ${hits.map((h) => "«" + h.query + "»").join(", ")} у реєстрі судових рішень.` : "") +
          (r.text_len ? ` Текст завантажено (${r.text_len} знаків).` : " Текст ще не завантажено — лише метадані з реєстру.") });
    }
    if (kind === "registry") {
      const r = (await sql`SELECT edrpou, name, short_name, stan, opf, founders, beneficiaries, signers, authorized_capital, matched_queries, updated_at FROM edr_orgs WHERE edrpou = ${key}`)[0];
      if (!r) return json(res, 404, { error: "no such record" });
      const decisions = await sql`SELECT count(*)::int n FROM court_decisions WHERE text ILIKE ${"%" + (r.short_name || r.name) + "%"}`.catch(() => [{ n: null }]);
      return json(res, 200, { kind, record: r, decisions_mentioning: decisions[0].n,
        explain: `Запис ЄДР ${r.edrpou}: ${r.name}${r.stan ? " · стан: " + r.stan : ""}. Засновників: ${(r.founders || []).length}, підписантів: ${(r.signers || []).length}, бенефіціарів: ${(r.beneficiaries || []).length}.` +
          (r.matched_queries?.length ? ` Знайдено за запитами: ${r.matched_queries.map((x) => "«" + x.replace(/^person:/, "") + "»").join(", ")}.` : "") });
    }
    if (kind === "decl" || kind === "fop") {
      const src = kind === "decl" ? "nazk_decl" : "edr_fop";
      const r = (await sql`SELECT query, title, snippet, url, payload, created_at FROM registry_hits WHERE source = ${src} AND ref = ${key}`)[0];
      if (!r) return json(res, 404, { error: "no such hit" });
      const p = r.payload || {};
      const explain = kind === "decl"
        ? `Декларація ${p.year || ""} року, подана ${p.name || ""}: ${p.work_place || "місце роботи не вказано"}${p.work_post ? ", " + p.work_post : ""}${p.region ? " (" + p.region + ")" : ""}. Знайдено в реєстрі декларацій НАЗК за запитом «${r.query}» — це однофамілець, доки не підтверджено по батькові та місцем роботи.`
        : `ФОП ${p.name || ""}: ${p.stan || ""}; реєстрація ${p.registration || "?"}${p.terminated ? "; припинення: " + p.terminated : ""}. Запис ЄДР (дамп data.gov.ua), знайдено за запитом «${r.query}». Адреси і КВЕД у відкритому дампі відсутні.`;
      return json(res, 200, { kind, record: { ...p, url: r.url, query: r.query, found_at: r.created_at }, explain });
    }
    if (kind === "case") {
      const decisions = await sql`SELECT judgment_form f, count(*)::int n FROM court_decisions WHERE lower(cause_num) = ${key} GROUP BY 1 ORDER BY 2 DESC`;
      const span = (await sql`SELECT count(*)::int n, min(adjudication_date) d0, max(adjudication_date) d1, count(DISTINCT court)::int courts, count(DISTINCT judge)::int judges, count(text)::int texts,
                               string_agg(DISTINCT court, ' · ') court_list FROM court_decisions WHERE lower(cause_num) = ${key}`)[0];
      const media = await sql`SELECT name_norm, role, position, quote, url, confidence FROM media_links WHERE lower(cause_num) = ${key} ORDER BY confidence DESC LIMIT 20`.catch(() => []);
      const timeline = await sql`SELECT doc_id, judgment_form f, adjudication_date d, court, judge FROM court_decisions WHERE lower(cause_num) = ${key} ORDER BY adjudication_date DESC LIMIT 40`;
      return json(res, 200, { kind, forms: decisions, summary: span, media, timeline,
        explain: span.n ? `Справа ${key}: ${span.n} рішень з ${span.d0} по ${span.d1}, ${span.courts} суд(ів): ${span.court_list}. Текстів завантажено: ${span.texts}.` : `Справа ${key}: рішень у базі ще немає.` });
    }
    if (kind === "person" || kind === "org") {
      const name = key;
      const like = "%" + name + "%";
      const [roles, hits, orgs, mentions, texts] = await Promise.all([
        sql`SELECT role, count(*)::int n FROM ee_persons WHERE lower(name_norm) = ${name} GROUP BY 1 ORDER BY 2 DESC`.catch(() => []),
        sql`SELECT source, count(*)::int n FROM registry_hits WHERE lower(query) = ${name} GROUP BY 1`.catch(() => []),
        sql`SELECT edrpou, short_name, stan FROM edr_orgs WHERE EXISTS (SELECT 1 FROM jsonb_array_elements_text(founders) f WHERE lower(f) LIKE ${like})
             OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(signers) f WHERE lower(f) LIKE ${like}) LIMIT 30`.catch(() => []),
        sql`SELECT count(*)::int n, min(mm.ts_published)::date d0, max(mm.ts_published)::date d1 FROM query_hits h JOIN messages mm ON mm.id = h.message_id WHERE lower(h.query) = ${name}`.catch(() => [{ n: 0 }]),
        sql`SELECT doc_id, cause_num, judgment_form f, adjudication_date d, court FROM court_decisions WHERE text ILIKE ${like} ORDER BY adjudication_date DESC LIMIT 15`.catch(() => []),
      ]);
      const parts = [];
      if (roles.length) parts.push("ролі у справах за текстами: " + roles.map((r) => `${r.role} ×${r.n}`).join(", "));
      for (const h of hits) parts.push(({ reyestr: "рішень судів з цим імʼям", nazk_decl: "декларацій НАЗК", edr_fop: "записів ФОП" }[h.source] || h.source) + ": " + h.n);
      if (orgs.length) parts.push("компаній у ЄДР (засновник/підписант): " + orgs.length);
      if (mentions[0]?.n) parts.push(`згадок у постах: ${mentions[0].n} (${mentions[0].d0}…${mentions[0].d1})`);
      if (texts.length) parts.push("текстів рішень, де зустрічається імʼя: " + texts.length);
      const explain = (kind === "person" ? "Особа" : "Організація") + ` «${name}». ` + (parts.length ? parts.join("; ") + "." : "Поки лише імʼя з запиту — реєстри нічого не повернули або ще не запитувались.") +
        (kind === "person" && name.split(" ").length < 3 ? " Імʼя без по батькові: усе, що нижче, може належати різним людям." : "");
      return json(res, 200, { kind, roles, hits, orgs, mentions: mentions[0], texts, explain });
    }
    if (kind === "post") {
      const r = (await sql`SELECT id, source_id, ts_published, url, text, threat_level, event_kind FROM messages WHERE id = ${key}`)[0];
      if (!r) return json(res, 404, { error: "no such post" });
      return json(res, 200, { kind, record: r, explain: `Пост ${r.source_id} від ${String(r.ts_published).slice(0, 10)}${r.event_kind ? " · " + r.event_kind : ""}.` });
    }
    return json(res, 200, { kind, explain: "" });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
