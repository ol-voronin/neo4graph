import { sql, json } from "./_db.js";
import { lookup } from "./_lake.js";
import { peopleOf } from "./registry.js";
import { expand } from "./_expand.js";
import { postMentions, webFound } from "./_mentions.js";
import { lawyerRows, lawyerList } from "./_lawyers.js";

// GET /api/node?id=<graph node id>&q=<name to highlight>
// GET /api/node?id=<id>&expand=1&label=<name>[&judge=1] -> an on-the-fly neighbourhood for an object not in the graph yet
//     (registry hit, ФОП, person, org, topic:<words>), in /api/graph?around= format plus `posts` (see _expand.js)
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

const ymd = (d) => { if (!d) return "?"; const x = d instanceof Date ? d : new Date(d); return isNaN(x) ? String(d).slice(0, 10) : new Date(x.getTime() - x.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };

export default async function handler(req, res) {
  const url = new URL(req.url, "http://x");
  const id = url.searchParams.get("id") || "";
  const q = url.searchParams.get("q") || "";
  const i = id.indexOf(":");
  const kind = id.slice(0, i), key = id.slice(i + 1);
  try {
    if (url.searchParams.get("expand")) return json(res, 200, await expand(id, url.searchParams.get("label") || "", { judge: url.searchParams.get("judge") === "1" }));
    if (kind === "topic") {
      const [posts, web] = await Promise.all([postMentions(key, { limit: 30, ms: 3000 }), webFound(key).catch(() => ({ items: [] }))]);
      return json(res, 200, { kind, posts, web: web.items,
        explain: posts.total ? `Згадки «${key}» у постах: ${posts.total}${posts.capped ? "+" : ""}${posts.all && posts.all !== posts.total ? ` (усі слова разом — у ${posts.all})` : ""}. Нижче — найновіші; на графі тема повʼязана з каналами та обʼєктами, які ці пости вже згадують.`
          : posts.skipped || `У постах «${key}» не згадується.` });
    }
    if (kind === "wanted") {
      const r = (await sql`SELECT id, last_name_u, first_name_u, middle_name_u, last_name_r, first_name_r, middle_name_r, birth_date::text birth_date, sex, category,
                           article_crim, restraint, ovd, agency, region, lost_date::text lost_date, lost_place, first_seen::text first_seen, removed::text removed
                           FROM wanted_persons WHERE id = ${key}`)[0];
      if (!r) return json(res, 404, { error: "no such wanted record" });
      const who = [r.last_name_u, r.first_name_u, r.middle_name_u].filter(Boolean).join(" ");
      // first look at a wanted person: queue the registry chain (court register, NAZK declarations with assets, ФОП, companies,
      // debtors) once per 30 days; the cloud drains 3 items per 10 min and the Ukrainian-IP worker polls every 2 min
      const full = who.toLowerCase().replace(/(^|[\s'’-])(\p{L})/gu, (_, a, b) => a + b.toUpperCase());
      const [q] = await sql`SELECT status, ua_status, created_at FROM query_queue WHERE lower(query) = lower(${full}) AND created_at > now() - interval '30 days' ORDER BY created_at DESC LIMIT 1`.catch(() => []);
      let chain = q ? (q.ua_status || q.status) : null;
      if (!q && full.split(" ").length >= 2) { await sql`INSERT INTO query_queue(query, kind) VALUES (${full}, 'person')`.catch(() => null); chain = "queued"; }
      const chainNote = chain === "queued" || chain === "running" ? " Запущено збір: судові рішення, декларації НАЗК (нерухомість, авто, компанії, доходи, родина), ФОП, ролі в компаніях, боржники — результати зʼявляться в графі за 10–30 хв."
        : chain ? " Збір по реєстрах уже виконано — результати в графі поруч з особою." : "";
      return json(res, 200, { kind, record: r, chain,
        explain: `${who}${r.birth_date ? ", нар. " + r.birth_date : ""} — ${(r.category || "у розшуку").toLowerCase()}${r.article_crim ? ", " + r.article_crim : ""}${r.restraint ? "; запобіжний захід: " + r.restraint.toLowerCase() : ""}.` +
          ` Шукає: ${r.ovd || "?"}. Переховується з ${r.lost_date || "?"}${r.lost_place ? ", " + r.lost_place.toLowerCase() : ""}.` +
          (r.removed ? ` Запис зник із відкритих даних ${r.removed} (затримано, знайдено або знято з розшуку — у даних не сказано).` : "") +
          " Джерело: відкриті дані Нацполіції (data.gov.ua). Збіг за ПІБ з іншими вузлами графа не означає, що це та сама людина." + chainNote });
    }
    if (kind === "article") {
      const [w, ytd] = await Promise.all([
        sql`SELECT count(*) FILTER (WHERE removed IS NULL)::int active, count(*)::int total, mode() WITHIN GROUP (ORDER BY article_crim) example,
            count(*) FILTER (WHERE agency = 'sbu' AND removed IS NULL)::int sbu, count(*) FILTER (WHERE agency = 'dbr' AND removed IS NULL)::int dbr
            FROM wanted_persons WHERE article = ${key}`.catch(() => [{}]),
        sql`WITH l AS (SELECT DISTINCT ON (key) key, period_to, value, derived FROM ogp_ytd WHERE form = 'forma_1' AND measure = 'registered' AND key = ${"art:" + key} ORDER BY key, period_to DESC)
            SELECT l.period_to::text period_to, l.value, p.value prev, k.label FROM l LEFT JOIN ogp_ytd p ON p.form = 'forma_1' AND p.measure = 'registered' AND p.key = l.key
            AND p.period_to = l.period_to - interval '1 year' LEFT JOIN ogp_keys k ON k.form = 'forma_1' AND k.key = l.key`.catch(() => []),
      ]);
      const a = w[0] || {}, y = ytd[0];
      return json(res, 200, { kind, wanted: a, ogp: y || null,
        explain: `Стаття ${key} КК${y?.label ? " — " + y.label : ""}. У розшуку Нацполіції зараз ${a.active || 0} осіб за цією статтею (з них шукає СБУ — ${a.sbu || 0}, ДБР — ${a.dbr || 0}).` +
          (!y ? " У звітах ОГП ця стаття окремим рядком не публікується."
            : new Date(y.period_to) < new Date(Date.now() - 400 * 864e5)
              ? ` У звітах Офісу Генпрокурора стаття востаннє опублікована за ${y.period_to.slice(0, 4)} рік (${y.value} зареєстровано); з 2022 військові злочини (розділ XIX) не публікуються — дивіться розрахункову оцінку на сторінці фактчеку.`
              : ` За звітом Офісу Генпрокурора з початку року по ${y.period_to.slice(0, 7)} зареєстровано ${y.value}${y.prev != null ? ` (торік за ті ж місяці — ${y.prev})` : ""}.`) +
          " На графі — найновіші записи розшуку за статтею." });
    }
    if (kind === "erdr") {
      const [d, items, arma] = await Promise.all([
        sql`SELECT count(*)::int n, array_agg(DISTINCT cause_num) causes, min(adjudication_date)::text d0, max(adjudication_date)::text d1, string_agg(DISTINCT court, ' · ') courts
            FROM court_decisions WHERE erdr @> ARRAY[${key}]::text[]`.catch(() => [{}]),
        sql`SELECT kind, label, action, holder, cause_num, decision_date::text d FROM case_assets WHERE erdr @> ARRAY[${key}]::text[] ORDER BY decision_date DESC LIMIT 40`.catch(() => []),
        sql`SELECT asset_id, description, kind, state, valuation::float valuation FROM arma_assets WHERE erdr @> ARRAY[${key}]::text[] LIMIT 40`.catch(() => []),
      ]);
      const x = d[0] || {};
      return json(res, 200, { kind, decisions: x, items, arma,
        explain: `Кримінальне провадження ${key} (номер у ЄРДР). Рішень у базі з цим номером: ${x.n || 0}${x.n ? ` (${x.d0}…${x.d1}; ${x.courts || ""})` : ""}. ` +
          `Майно, назване в ухвалах: ${items.length}; активів у реєстрі АРМА: ${arma.length}. Підозрюваних реєстр знеособлює — привʼязка до людини лише через справу, в якій її названо.` });
    }
    if (kind === "arma") {
      const r = (await sql`SELECT * FROM arma_assets WHERE asset_id = ${key}`.catch(() => []))[0];
      if (!r) return json(res, 404, { error: "no such ARMA asset" });
      return json(res, 200, { kind, record: r,
        explain: `Актив у Єдиному державному реєстрі арештованих активів (АРМА): ${r.description || ""}${r.brand ? ", " + r.brand : ""}${r.model ? " " + r.model : ""}${r.year ? " " + r.year : ""}. ` +
          `Стан: ${r.state || "?"}${r.valuation ? `; оцінка ${Math.round(r.valuation).toLocaleString("uk-UA")} грн` : ""}. Рішення: ${(r.docnums || []).join(", ")}; провадження: ${(r.erdr || []).join(", ")}. Власника реєстр не публікує.` });
    }
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
      if (!r) {  // not in the graph yet: the card from the lake's registry index (found via /api/registry)
        const hit = /^\d{8}$/.test(key) ? (await lookup("ids", key, { exact: true }).catch(() => ({ rows: [] }))).rows[0] : null;
        if (!hit) return json(res, 404, { error: "no such record" });
        const people = await peopleOf(key).catch(() => []);
        const pick = (role) => people.filter((p) => p.role === role).map((p) => p.detail || p.name);
        return json(res, 200, { kind, record: { founders: pick("founder"), beneficiaries: pick("beneficiary"), signers: pick("signer"), edrpou: hit.code, name: hit.full_name || hit.name, short_name: hit.name, stan: hit.stan, registration: hit.since,
          region: hit.region, address: hit.address, contracts_won: hit.n_won, contracts_placed: hit.n_bought, debts: hit.debts, src: hit.src }, in_graph: false,
          explain: `Запис ЄДР ${hit.code}: ${hit.full_name || hit.name}${hit.stan ? " · стан: " + hit.stan : ""}${hit.since ? " · з " + hit.since : ""}.` +
            (hit.address ? ` Адреса за даними ProZorro: ${hit.address}.` : "") + (hit.n_won || hit.n_bought ? ` Договорів у ProZorro: постачальник ${hit.n_won}, замовник ${hit.n_bought}.` : "") +
            (hit.debts ? ` Виконавчих проваджень у реєстрі боржників: ${hit.debts}.` : "") + (people.length ? ` Засновників, бенефіціарів і підписантів у відкритому ЄДР: ${people.length}.` : " Засновники й підписанти — у наступній збірці індексу реєстрів.") });
      }
      const decisions = await sql`SELECT count(*)::int n FROM court_decisions WHERE text IS NOT NULL AND text ILIKE ${"%" + (r.short_name || r.name) + "%"}`.catch(() => [{ n: null }]);
      return json(res, 200, { kind, record: r, decisions_mentioning: decisions[0].n,
        explain: `Запис ЄДР ${r.edrpou}: ${r.name}${r.stan ? " · стан: " + r.stan : ""}. Засновників: ${(r.founders || []).length}, підписантів: ${(r.signers || []).length}, бенефіціарів: ${(r.beneficiaries || []).length}.` +
          (r.matched_queries?.length ? ` Знайдено за запитами: ${r.matched_queries.map((x) => "«" + x.replace(/^person:/, "") + "»").join(", ")}.` : "") });
    }
    if (kind === "decl" || kind === "fop" || kind === "fopx" || kind === "orgx") {
      const src = { decl: "nazk_decl", fop: "edr_fop", fopx: "yc_fop", orgx: "yc_org" }[kind];
      const r = (await sql`SELECT query, title, snippet, url, payload, created_at FROM registry_hits WHERE source = ${src} AND ref = ${key}`)[0];
      if (!r) return json(res, 404, { error: "no such hit" });
      const p = r.payload || {};
      const explain = kind === "decl"
        ? `Декларація ${p.year || ""} року, подана ${p.name || ""}: ${p.work_place || "місце роботи не вказано"}${p.work_post ? ", " + p.work_post : ""}${p.region ? " (" + p.region + ")" : ""}. Знайдено в реєстрі декларацій НАЗК за запитом «${r.query}» — це однофамілець, доки не підтверджено по батькові та місцем роботи.`
        : kind === "orgx"
        ? `Картка компанії ${p.short_name || p.name || ""} (ЄДРПОУ ${p.edrpou}): ${p.status || ""}; реєстрація ${p.registration || "?"}; керівник ${p.head || "?"}${p.head_since ? " з " + p.head_since : ""}. Основний КВЕД: ${p.kved_main || "?"}${(p.kved_other || []).length ? "; інші: " + p.kved_other.slice(0, 6).join("; ") : ""}. ${p.assets ? "У компанії " + p.assets + ". " : ""}${p.court_docs ? "Згадується у " + p.court_docs + " судових документах. " : ""}Дані ЄДР у переказі YouControl (публічна сторінка), знайдено за запитом «${r.query}».`
        : kind === "fopx"
        ? `ФОП ${p.name || ""}: ${p.status || ""}; реєстрація ${p.registration || "?"}${p.city ? "; " + p.city : ""}. Основний КВЕД: ${p.kved_main || "?"}${(p.kved_other || []).length ? "; інші: " + p.kved_other.join("; ") : ""}. Дані ЄДР у переказі YouControl (публічна сторінка), знайдено за запитом «${r.query}».`
        : `ФОП ${p.name || ""}: ${p.stan || ""}; реєстрація ${p.registration || "?"}${p.terminated ? "; припинення: " + p.terminated : ""}. Запис ЄДР (дамп data.gov.ua), знайдено за запитом «${r.query}». КВЕД і адреса у відкритому дампі відсутні — дивіться картку «ФОП … · КВЕД» поруч або YouControl.`;
      return json(res, 200, { kind, record: { ...p, url: r.url, query: r.query, found_at: r.created_at }, explain });
    }
    if (kind === "case") {
      const decisions = await sql`SELECT judgment_form f, count(*)::int n FROM court_decisions WHERE lower(cause_num) = ${key} GROUP BY 1 ORDER BY 2 DESC`;
      const span = (await sql`SELECT count(*)::int n, min(adjudication_date) d0, max(adjudication_date) d1, count(DISTINCT court)::int courts, count(DISTINCT judge)::int judges, count(text)::int texts,
                               string_agg(DISTINCT court, ' · ') court_list FROM court_decisions WHERE lower(cause_num) = ${key}`)[0];
      const media = await sql`SELECT name_norm, role, position, quote, url, confidence FROM media_links WHERE lower(cause_num) = ${key} ORDER BY confidence DESC LIMIT 20`.catch(() => []);
      const timeline = await sql`SELECT doc_id, judgment_form f, adjudication_date d, court, judge FROM court_decisions WHERE lower(cause_num) = ${key} ORDER BY adjudication_date DESC LIMIT 40`;
      // lawyers named in the texts (person_case_links): 'можливо' = the only one of that name in the register (linked),
      // 'неоднозначно' = several candidates, 'немає в реєстрі' = none (both shown as text only)
      const lawyers = lawyerList(await lawyerRows({ cases: [key] }));
      const probable = lawyers.filter((l) => l.certainty === "можливо").length;
      return json(res, 200, { kind, forms: decisions, summary: span, media, timeline, lawyers,
        explain: (span.n ? `Справа ${key}: ${span.n} рішень з ${ymd(span.d0)} по ${ymd(span.d1)}, ${span.courts} суд(ів): ${span.court_list}. Текстів завантажено: ${span.texts}.` : `Справа ${key}: рішень у базі ще немає.`) +
          (lawyers.length ? ` У текстах рішень названо адвокатів: ${lawyers.length}${probable ? ` (з них ${probable} — можливо ті самі, що в реєстрі адвокатів: єдиний збіг за ПІБ)` : ""}.` : "") });
    }
    if (kind === "person" || kind === "org") {
      const name = key;
      const like = "%" + name + "%";
      const [roles, hits, orgs, mentions, texts, lrows] = await Promise.all([
        sql`SELECT role, count(*)::int n FROM ee_persons WHERE lower(name_norm) = ${name} GROUP BY 1 ORDER BY 2 DESC`.catch(() => []),
        sql`SELECT source, count(*)::int n FROM registry_hits WHERE lower(query) = ${name} GROUP BY 1`.catch(() => []),
        sql`SELECT edrpou, short_name, stan FROM edr_orgs WHERE EXISTS (SELECT 1 FROM jsonb_array_elements_text(founders) f WHERE lower(f) LIKE ${like})
             OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(signers) f WHERE lower(f) LIKE ${like}) LIMIT 30`.catch(() => []),
        sql`SELECT count(*)::int n, min(mm.ts_published)::date d0, max(mm.ts_published)::date d1 FROM query_hits h JOIN messages mm ON mm.id = h.message_id WHERE lower(h.query) = ${name}`.catch(() => [{ n: 0 }]),
        sql`SELECT doc_id, cause_num, judgment_form f, adjudication_date d, court FROM court_decisions WHERE text IS NOT NULL AND text ILIKE ${like} ORDER BY adjudication_date DESC LIMIT 15`.catch(() => []),
        kind === "person" ? lawyerRows({ persons: [id] }) : [],
      ]);
      const lawyer_cases = lawyerList(lrows);
      const parts = [];
      if (roles.length) parts.push("ролі у справах за текстами: " + roles.map((r) => `${r.role} ×${r.n}`).join(", "));
      for (const h of hits) parts.push(({ reyestr: "рішень судів з цим імʼям", nazk_decl: "декларацій НАЗК", edr_fop: "записів ФОП" }[h.source] || h.source) + ": " + h.n);
      if (orgs.length) parts.push("компаній у ЄДР (засновник/підписант): " + orgs.length);
      if (mentions[0]?.n) parts.push(`згадок у постах: ${mentions[0].n} (${ymd(mentions[0].d0)}…${ymd(mentions[0].d1)})`);
      if (texts.length) parts.push("текстів рішень, де зустрічається імʼя: " + texts.length);
      if (lawyer_cases.length) parts.push(`адвокат у справах (за текстами рішень; можливо та сама особа — єдиний збіг за ПІБ у реєстрі адвокатів): ${new Set(lawyer_cases.map((l) => l.cause_num)).size}`);
      const explain = (kind === "person" ? "Особа" : "Організація") + ` «${name}». ` + (parts.length ? parts.join("; ") + "." : "Поки лише імʼя з запиту — реєстри нічого не повернули або ще не запитувались.") +
        (kind === "person" && name.split(" ").length < 3 ? " Імʼя без по батькові: усе, що нижче, може належати різним людям." : "");
      return json(res, 200, { kind, roles, hits, orgs, mentions: mentions[0], texts, lawyer_cases, explain });
    }
    if (kind === "web") {
      const r = (await sql`SELECT query, url, title, snippet, engine, domain, fetched_at FROM web_mentions WHERE url = ${key} ORDER BY fetched_at DESC LIMIT 1`)[0];
      if (!r) return json(res, 404, { error: "no such page" });
      const txt = (r.title || "") + " " + (r.snippet || "");
      const sur = (r.query || "").split(/\s+/)[0];
      const names = [...new Set((txt.match(/\p{Lu}\p{Ll}+(?:[-'’]\p{Lu}?\p{Ll}+)?\s+\p{Lu}\p{Ll}+(?:\s+\p{Lu}\p{Ll}+(?:ович|івна|ївна|евич|йович|івич))?/gu) || []).filter((n) => n.split(" ")[0].slice(0, 4).toLowerCase() === sur.slice(0, 4).toLowerCase()))];
      const ingested = (await sql`SELECT id FROM messages WHERE url = ${key} LIMIT 1`.catch(() => []))[0];
      return json(res, 200, { kind, record: r, names, ingested: !!ingested,
        explain: `Сторінка ${r.domain}, знайдена пошуком «${r.query}» (${r.engine}). ${ingested ? "Сторінку вже взято в базу як джерело: люди й організації з неї — у графі." : "Поки це лише посилання зі сніпетом: щоб витягти з неї людей, посади й організації, візьміть сторінку в базу."}` });
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
