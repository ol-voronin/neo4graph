import { query, json } from "./_db.js";
import { norm } from "./_graph.js";
import { searchAll } from "./search.js";
import { qkey } from "./feedback.js";

// GET /api/aisearch?q=Кравченко Олександр Сергійович, ФОП, Київ, принти
// AI search: the ordinary search runs on the name in the query, then the model reads every candidate (graph, registries,
// wanted list, courts) together with the rest of the query and the researcher's earlier verdicts (link_feedback,
// search_picks; migration 034) and splits them into «the same person» groups and namesakes, saying why. It only sorts what
// the databases returned: it does not add facts, and a group is a hypothesis to check, not an identification.
const MODEL = "claude-haiku-5-5";
const SYSTEM = `Ти допомагаєш дослідникові антикорупційних даних розібрати збіги пошуку. Тобі дають запит дослідника (ПІБ і, можливо,
уточнення: регіон, рід занять, рік народження, компанія) і пронумерований список кандидатів із баз (граф, ЄДР/ФОП, розшук, суди).
Завдання: згрупувати кандидатів, які ймовірно стосуються ОДНІЄЇ людини з запиту, і відокремити однофамільців.
Правила:
- Спирайся лише на те, що є в рядках кандидатів і в запиті. Нічого не вигадуй.
- Інше по батькові, інший рік народження, інше імʼя = інша людина. Без по батькові — лише "можливо".
- Вердикти дослідника обовʼязкові: "no" — це НЕ та людина / хибний звʼязок; "yes" і "picked" — підтверджено.
- Однакові ПІБ у різних регіонах без інших збігів — окремі групи або однофамільці, а не одна людина.
Відповідай ЛИШЕ JSON без пояснень навколо:
{"profile":{"name":"","patronymic":"","region":"","role":"","birth_year":"","other":""},
 "groups":[{"title":"коротко хто це","confidence":"висока|середня|низька","why":"1-2 речення: які збіги","items":[номери]}],
 "namesakes":{"why":"чому відкинуто","items":[номери]},
 "ask":"одне уточнення, яке найкраще розділить решту, або пусто"}`;

const nameOf = (q) => {
  const w = norm(q).split(/[\s,;]+/).filter(Boolean);
  const i = w.findIndex((x) => /^\p{Lu}/u.test(x));
  const run = [];
  for (let j = Math.max(0, i); j < w.length && run.length < 3 && /^\p{Lu}[\p{L}'’ʼ-]+$/u.test(w[j]); j++) run.push(w[j]);
  return run.length ? run.join(" ") : norm(q).split(",")[0];
};

async function ask(user) {
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) throw new Error("AI-пошук недоступний: немає ключа моделі");
  for (let attempt = 0; attempt < 2; attempt++) {
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST", signal: AbortSignal.timeout(40000),
      headers: { "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01",
        ...(process.env.ANTHROPIC_WORKSPACE_ID ? { "anthropic-workspace-id": process.env.ANTHROPIC_WORKSPACE_ID } : {}) },
      body: JSON.stringify({ model: MODEL, max_tokens: 3000, system: SYSTEM, messages: [{ role: "user", content: user }], output_config: { effort: "low" } }),
    });
    const d = await r.json();
    if (d.error) throw new Error(d.error.message || "model error");
    const t = (d.content || []).filter((b) => b.type === "text").map((b) => b.text).join("");
    const m = t.match(/\{[\s\S]*\}/);
    try { if (m) return JSON.parse(m[0]); } catch (_) { /* one retry on malformed JSON */ }
  }
  throw new Error("модель відповіла не JSON");
}

export default async function handler(req, res) {
  const url = new URL(req.url, "http://x");
  const q = norm(url.searchParams.get("q") || "");
  if (q.length < 3 || q.length > 300) return json(res, 400, { error: "q length" });
  try {
    const name = nameOf(q);
    const s = await searchAll(name, "all", "main");
    const items = [];
    for (const g of s.groups) if (["graph", "registry", "wanted", "courts"].includes(g.key)) for (const x of g.items.slice(0, 14)) items.push({ ...x, group: g.key });
    if (!items.length) return json(res, 200, { q, name, groups: [], namesakes: { items: [] }, items: [], note: "у базах нічого не знайдено за цим імʼям" });
    const ids = items.map((x) => x.id);
    const [fb, picks] = await Promise.all([
      query(`SELECT a, b, rel, verdict FROM link_feedback WHERE a = ANY($1) OR b = ANY($1) LIMIT 200`, [ids]).catch(() => []),
      query(`SELECT id, n FROM search_picks WHERE q = $1 OR q = $2`, [qkey(q), qkey(name)]).catch(() => []),
    ]);
    const GL = { graph: "граф", registry: "ЄДР/ФОП", wanted: "розшук", courts: "суди" };
    const lines = items.map((x, i) => `${i + 1}. [${GL[x.group]}] ${x.label}${x.sub ? " — " + x.sub : ""}`).join("\n");
    const idx = (id) => ids.indexOf(id) + 1;
    const fbl = [...fb.map((f) => `${f.verdict}: ${idx(f.a) || f.a} — ${f.rel} — ${idx(f.b) || f.b}`), ...picks.map((p) => `picked: ${idx(p.id) || p.id} (${p.n} раз)`)].join("\n");
    const out = await ask(`Запит дослідника: ${q}\n\nКандидати:\n${lines}\n\nВердикти дослідника:\n${fbl || "немає"}`);
    const pickN = (arr) => [...new Set((arr || []).map((n) => +n).filter((n) => n >= 1 && n <= items.length))].map((n) => items[n - 1].id);
    const groups = (out.groups || []).map((g) => ({ title: String(g.title || ""), confidence: String(g.confidence || ""), why: String(g.why || ""), ids: pickN(g.items) })).filter((g) => g.ids.length);
    return json(res, 200, { q, name, profile: out.profile || {}, groups, namesakes: { why: String(out.namesakes?.why || ""), ids: pickN(out.namesakes?.items) },
      ask: String(out.ask || ""), items, model: MODEL, feedback: fb.length + picks.length });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
