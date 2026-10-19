import { sql, json, classify, CAUSE_RE } from "./_db.js";

// POST /api/chat {messages:[{role,content}]} -> answer + structured results. Claude Haiku with tools over the database.
const SYSTEM = `Ти — асистент дослідницької бази про українські антикорупційні справи, судові рішення, заяви сторін і публічну риторику.
Відповідай українською (або мовою користувача), стисло, з конкретикою: номери справ, суди, судді, дати, назви компаній, ЄДРПОУ.
Користуйся інструментами, щоб шукати в базі; не вигадуй даних. Якщо в базі потрібного зрізу ще немає (наприклад, рішення іншого року чи категорії),
скажи це прямо і постав у чергу індексацію або ланцюжок по реєстрах через enqueue, пояснивши, що результат з'явиться за 10–30 хвилин.
Категорії справ у реєстрі — українські назви (наприклад, "земельні" справи: шукай за фрагментом "земел"). Рік із фрази "за 25-й рік" = 2025.
У відповіді перелічуй знайдене списком; для справ давай номер, суд, кількість рішень, діапазон дат.
Номер справи (формат 991/1234/21) → get_case: хронологія рішень із посиланнями. Якщо рішень у базі немає, get_case сам ставить справу в чергу.
"Знайди ФОП …" → find_fop з повним імʼям. Це лише запис ЄДР ФОП (стан, дата реєстрації, припинення); збіг за імʼям може бути однофамільцем — кажи це.
Не зводь ФОП із судами, боргами чи іншими реєстрами в одну відповідь про людину і не роби висновків про вину конкретних осіб.`;

const TOOLS = [
  { name: "search_decisions", description: "Пошук судових рішень у базі (індекс ЄДРСР). Повертає групування по справах або список рішень.",
    input_schema: { type: "object", properties: {
      year: { type: "integer" }, category: { type: "string", description: "фрагмент назви категорії, напр. 'земел'" },
      court: { type: "string", description: "фрагмент назви суду" }, judge: { type: "string" }, form: { type: "string", description: "Вирок|Ухвала|Рішення|Постанова" },
      cause_num: { type: "string" }, justice_kind: { type: "string", description: "Кримінальне|Цивільне|Адміністративне|Господарське" },
      group_by_case: { type: "boolean", default: true }, limit: { type: "integer", default: 30 } } } },
  { name: "list_categories", description: "Які категорії справ і суди є в індексі (з кількістю), щоб зрозуміти покриття.", input_schema: { type: "object", properties: { year: { type: "integer" }, like: { type: "string" } } } },
  { name: "search_entities", description: "Люди, компанії (з ЄДРПОУ), справи й активи з антикорупційних постів.", input_schema: { type: "object", properties: { q: { type: "string" } }, required: ["q"] } },
  { name: "search_corpus", description: "Повнотекстовий пошук по постах, новинах, заявах і текстах рішень у базі.", input_schema: { type: "object", properties: { q: { type: "string" }, since: { type: "string" }, until: { type: "string" }, limit: { type: "integer", default: 15 } }, required: ["q"] } },
  { name: "search_statements", description: "Заяви сторін (загрози, обіцянки, прогнози) з вердиктом резолвера і послужний список акторів.", input_schema: { type: "object", properties: { actor_side: { type: "string" }, q: { type: "string" }, limit: { type: "integer", default: 20 } } } },
  { name: "get_case", description: "Картка справи за номером (напр. 991/1234/21): усі рішення в базі — дати, форми, суди, судді, посилання на тексти. Якщо рішень немає — ставить справу в чергу.",
    input_schema: { type: "object", properties: { cause_num: { type: "string" } }, required: ["cause_num"] } },
  { name: "find_fop", description: "Запис ЄДР про ФОП за повним імʼям (прізвище імʼя по батькові): стан, дата реєстрації, припинення. Якщо ще не шукали — ставить пошук у чергу (результат за 5–10 хв).",
    input_schema: { type: "object", properties: { name: { type: "string" } }, required: ["name"] } },
  { name: "enqueue", description: "Поставити в чергу роботу з реєстрами: kind=case (номер справи), person, org, edrpou, text, web (згадки в інтернеті), category_index (query у форматі 'РІК|фрагмент категорії|форма?' напр. '2025|земел' — індексує всі такі рішення року).",
    input_schema: { type: "object", properties: { query: { type: "string" }, kind: { type: "string", enum: ["case", "person", "org", "edrpou", "text", "web", "category_index", "fop"] } }, required: ["query", "kind"] } },
];

async function enqueue(q, kind) {
  const open = await sql`SELECT id, status FROM query_queue WHERE query = ${q} AND kind = ${kind} AND status IN ('queued','running') LIMIT 1`;
  if (open.length) return { queued: true, existing: true, ...open[0] };
  const row = await sql`INSERT INTO query_queue(query, kind) VALUES (${q}, ${kind}) RETURNING id, status, created_at`;
  return { queued: true, ...row[0], note: "Черга обробляється кожні 5 хв; пошук ФОП — 5–10 хв, справа чи індексація зрізу року — 5–20 хв." };
}

async function runTool(name, a) {
  if (name === "search_decisions") {
    const lim = Math.min(100, a.limit || 30);
    const where = [];
    const params = [];
    const add = (cond, v) => { params.push(v); where.push(cond.replace("?", "$" + params.length)); };
    if (a.year) add("year = ?", a.year);
    if (a.category) add("category ILIKE ?", "%" + a.category + "%");
    if (a.court) add("court ILIKE ?", "%" + a.court + "%");
    if (a.judge) add("judge ILIKE ?", "%" + a.judge + "%");
    if (a.form) add("judgment_form ILIKE ?", "%" + a.form + "%");
    if (a.cause_num) add("cause_num = ?", a.cause_num);
    if (a.justice_kind) add("justice_kind ILIKE ?", "%" + a.justice_kind + "%");
    const w = where.length ? "WHERE " + where.join(" AND ") : "";
    if (a.group_by_case !== false) {
      const rows = await sql.query(`SELECT cause_num, max(court) court, count(*)::int n, min(adjudication_date) d0, max(adjudication_date) d1, max(category) category, string_agg(DISTINCT judgment_form, ', ') forms FROM court_decisions ${w} GROUP BY cause_num ORDER BY n DESC LIMIT ${lim}`, params);
      const tot = await sql.query(`SELECT count(*)::int n, count(DISTINCT cause_num)::int cases FROM court_decisions ${w}`, params);
      return { total_decisions: tot[0].n, total_cases: tot[0].cases, cases: rows };
    }
    return { decisions: await sql.query(`SELECT doc_id, cause_num, court, judgment_form, category, adjudication_date, judge, doc_url FROM court_decisions ${w} ORDER BY adjudication_date DESC LIMIT ${lim}`, params) };
  }
  if (name === "list_categories") {
    const rows = await sql`SELECT year, category, count(*)::int n FROM court_decisions WHERE (${a.year || null}::int IS NULL OR year = ${a.year || null}) AND (${a.like || ""} = '' OR category ILIKE ${"%" + (a.like || "") + "%"}) GROUP BY 1,2 ORDER BY n DESC LIMIT 40`;
    const courts = await sql`SELECT year, court, count(*)::int n FROM court_decisions GROUP BY 1,2 ORDER BY n DESC LIMIT 15`;
    return { categories: rows, courts };
  }
  if (name === "search_entities") {
    const pat = "%" + a.q + "%";
    return {
      persons: await sql`SELECT name_norm, string_agg(DISTINCT role, ',') roles, count(*)::int n, max(position) position FROM ee_persons WHERE name_norm ILIKE ${pat} OR position ILIKE ${pat} GROUP BY 1 LIMIT 15`,
      orgs: await sql`SELECT name, max(edrpou) edrpou, count(*)::int n FROM ee_orgs WHERE name ILIKE ${pat} GROUP BY 1 LIMIT 15`,
      edr: await sql`SELECT edrpou, short_name, stan, founders, signers FROM edr_orgs WHERE name ILIKE ${pat} OR short_name ILIKE ${pat} OR founders::text ILIKE ${pat} OR signers::text ILIKE ${pat} LIMIT 10`,
      cases: await sql`SELECT nickname, agency, stage, summary FROM ee_cases WHERE nickname ILIKE ${pat} OR summary ILIKE ${pat} LIMIT 10`,
      assets: await sql`SELECT type, description, linked_person FROM ee_assets WHERE description ILIKE ${pat} OR linked_person ILIKE ${pat} LIMIT 10`,
      web: await sql`SELECT title, url, snippet FROM web_mentions WHERE query ILIKE ${pat} OR title ILIKE ${pat} LIMIT 10`.catch(() => []),
    };
  }
  if (name === "search_corpus") {
    const pat = "%" + a.q + "%";
    const lim = Math.min(40, a.limit || 15);
    const rows = await sql`SELECT m.id, m.source_id, m.ts_published, m.url, left(regexp_replace(m.text,'\s+',' ','g'), 300) snippet FROM messages m
      WHERE m.text ILIKE ${pat} AND (${a.since || null}::timestamptz IS NULL OR m.ts_published >= ${a.since || null}) AND (${a.until || null}::timestamptz IS NULL OR m.ts_published <= ${a.until || null})
      ORDER BY m.ts_published DESC LIMIT ${lim}`;
    const tot = await sql`SELECT count(*)::int n FROM messages WHERE text ILIKE ${pat}`;
    return { total: tot[0].n, items: rows };
  }
  if (name === "search_statements") {
    const pat = "%" + (a.q || "") + "%";
    const rows = await sql`SELECT ts_published, actor_side, actor_name, stype, quote, predicted_event, target_region, status, resolver_note FROM statements
      WHERE (${a.actor_side || ""} = '' OR actor_side = ${a.actor_side || ""}) AND (${a.q || ""} = '' OR quote ILIKE ${pat} OR predicted_event ILIKE ${pat} OR actor_name ILIKE ${pat})
      ORDER BY ts_published DESC LIMIT ${Math.min(50, a.limit || 20)}`;
    const track = await sql`SELECT * FROM actor_track_record ORDER BY n_resolved DESC LIMIT 30`;
    return { statements: rows, track_record: track };
  }
  if (name === "get_case") {
    const num = (a.cause_num || "").trim();
    if (!CAUSE_RE.test(num)) return { error: "номер справи має вигляд 991/1234/21" };
    const key = num.toLowerCase();
    const [sum] = await sql`SELECT count(*)::int n, min(adjudication_date) d0, max(adjudication_date) d1, count(DISTINCT court)::int courts,
                                   string_agg(DISTINCT court, ' · ') court_list, string_agg(DISTINCT judge, ' · ') judges, count(text)::int texts,
                                   max(category) category, max(justice_kind) justice_kind FROM court_decisions WHERE lower(cause_num) = ${key}`;
    if (!sum.n) return { found: 0, ...(await enqueue(num, "case")) };
    const forms = await sql`SELECT judgment_form f, count(*)::int n FROM court_decisions WHERE lower(cause_num) = ${key} GROUP BY 1 ORDER BY 2 DESC`;
    const timeline = await sql`SELECT adjudication_date d, judgment_form f, court, judge, doc_url FROM court_decisions WHERE lower(cause_num) = ${key} ORDER BY adjudication_date DESC NULLS LAST LIMIT 40`;
    return { found: sum.n, summary: sum, forms, timeline };
  }
  if (name === "find_fop") {
    const q = (a.name || "").replace(/ФОП/gi, "").replace(/\s+/g, " ").trim();
    if (q.split(" ").length < 2) return { error: "потрібне повне імʼя: прізвище та імʼя (краще з по батькові)" };
    const rows = await sql`SELECT title, snippet, payload FROM registry_hits WHERE source = 'edr_fop' AND lower(query) = lower(${q}) ORDER BY title LIMIT 20`;
    if (rows.length) return { found: rows.length, records: rows.map((r) => r.payload), note: "Збіг за імʼям: це можуть бути різні люди (однофамільці)." };
    return { found: 0, ...(await enqueue(q, "fop")) };
  }
  if (name === "enqueue") return enqueue((a.query || "").trim(), a.kind);
  return { error: "unknown tool" };
}

export default async function handler(req, res) {
  if (req.method !== "POST") return json(res, 405, { error: "POST" });
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) return json(res, 503, { error: "Чат ще не підключено: на Vercel немає ANTHROPIC_API_KEY." });
  let body = "";
  for await (const c of req) body += c;
  let messages;
  try { messages = JSON.parse(body).messages.slice(-12); } catch { return json(res, 400, { error: "bad body" }); }
  const trace = [];
  try {
    for (let step = 0; step < 6; step++) {
      const r = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: { "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01" },
        body: JSON.stringify({ model: "claude-haiku-5-5", max_tokens: 4000, system: SYSTEM, tools: TOOLS, messages, output_config: { effort: "low" } }),
      });
      const data = await r.json();
      if (data.error) return json(res, 502, { error: data.error.message || "api error" });
      const toolUses = (data.content || []).filter((b) => b.type === "tool_use");
      if (!toolUses.length || data.stop_reason !== "tool_use") {
        const text = (data.content || []).filter((b) => b.type === "text").map((b) => b.text).join("\n");
        return json(res, 200, { answer: text, trace });
      }
      messages = [...messages, { role: "assistant", content: data.content }];
      const results = [];
      for (const tu of toolUses) {
        let out;
        try { out = await runTool(tu.name, tu.input || {}); } catch (e) { out = { error: String(e.message || e) }; }
        trace.push({ tool: tu.name, input: tu.input, summary: summarize(out) });
        results.push({ type: "tool_result", tool_use_id: tu.id, content: JSON.stringify(out).slice(0, 12000) });
      }
      messages = [...messages, { role: "user", content: results }];
    }
    return json(res, 200, { answer: "Забагато кроків; уточніть запит.", trace });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e), trace });
  }
}

function summarize(o) {
  if (!o || typeof o !== "object") return o;
  const s = {};
  for (const [k, v] of Object.entries(o)) s[k] = Array.isArray(v) ? v.slice(0, 20) : v;
  return s;
}
