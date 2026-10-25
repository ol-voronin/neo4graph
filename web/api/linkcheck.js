import { lookup } from "node:dns/promises";
import { isIP } from "node:net";
import { query, json } from "./_db.js";

// Any link -> what is on it, who is named, and a fact-check (migration 035).
// GET /api/linkcheck?url=...            -> {info}: title, author, text, names found (fast, no model)
// GET /api/linkcheck?url=...&check=1    -> {info, result}: the model reads the page, searches the web (Anthropic web search
//                                          tool) and answers: claims with verdicts and sources, plus signs of AI generation.
//                                          Cached per URL for 24 h (&fresh=1 re-checks).
// Instagram and TikTok pages are mostly closed to servers: TikTok / YouTube / X give a caption through oEmbed, Instagram
// only what its preview tags show. The page says what could be read, so a verdict on a caption alone reads as such.
const MODEL = "claude-haiku-5-5";
const UA = "Mozilla/5.0 (compatible; facebookexternalhit/1.1; +http://www.facebook.com/externalhit_uatext.php)";

// no requests to the private network (the function runs next to other services)
async function safeUrl(raw) {
  let u;
  try { u = new URL(raw); } catch { throw new Error("це не посилання"); }
  if (!/^https?:$/.test(u.protocol)) throw new Error("лише http(s) посилання");
  if (/^(localhost|.*\.local|.*\.internal)$/i.test(u.hostname)) throw new Error("закрита адреса");
  const ips = isIP(u.hostname) ? [u.hostname] : (await lookup(u.hostname, { all: true })).map((a) => a.address);
  if (ips.some((ip) => /^(10\.|127\.|0\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|::1$|f[cd]|fe80)/i.test(ip))) throw new Error("закрита адреса");
  return u;
}

const BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36";
async function get(url, { asJson = false, ms = 8000 } = {}) {
  const go = (ua) => fetch(url, { redirect: "follow", signal: AbortSignal.timeout(ms), headers: { "user-agent": ua, "accept-language": "uk,ru;q=0.8,en;q=0.6", accept: asJson ? "application/json" : "text/html,*/*" } });
  // the preview-bot agent opens Instagram / Facebook previews; news sites that refuse it get an ordinary browser agent
  let r = await go(UA);
  if (r.status === 403 || r.status === 429) r = await go(BROWSER);
  if (!r.ok) throw new Error("HTTP " + r.status);
  if (asJson) return r.json();
  const buf = await r.arrayBuffer();
  return new TextDecoder("utf-8").decode(buf.slice(0, 1_500_000));
}

const ent = (s) => String(s || "").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#39;|&#x27;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(+n)).replace(/&nbsp;/g, " ");
const meta = (html, k) => ent((html.match(new RegExp(`<meta[^>]+(?:property|name)=["']${k}["'][^>]*content=["']([^"']*)`, "i")) || html.match(new RegExp(`<meta[^>]+content=["']([^"']*)["'][^>]*(?:property|name)=["']${k}["']`, "i")) || [])[1] || "");
const textOf = (html) => ent(html.replace(/<(script|style|noscript|svg|nav|footer|header|aside)[\s\S]*?<\/\1>/gi, " ").replace(/<\/(p|div|h\d|li|br|tr)>/gi, "\n").replace(/<[^>]+>/g, " "))
  .replace(/[ \t]+/g, " ").replace(/\n\s*\n+/g, "\n").trim();

// «Прізвище Імʼя [По батькові]» / «Імʼя Прізвище» in Cyrillic; places and institutions are dropped
// one of the two words has to be a first name (stems cover the case forms: «Віталія», «Олені»)
const FIRST = /^(Олександр|Олексі|Андрі|Анатолі|Антон|Арсені|Артем|Богдан|Борис|Вадим|Валері|Валенти|Василь|Василя|Віктор|Віталі|Владислав|Володимир|Вячеслав|В'ячеслав|Геннаді|Георгі|Григор|Денис|Дмитр|Євген|Іван|Ігор|Ілля|Іллі|Кирил|Костянтин|Леонід|Максим|Микол|Михайл|Назар|Олег|Павл|Петр|Петро|Роман|Руслан|Сергі|Станіслав|Степан|Тарас|Тимур|Юрі|Ярослав|Яна|Аліна|Алла|Анастасі|Ангелін|Анн|Валентин|Вікторі|Галин|Дарі|Ірин|Катерин|Ксені|Лариса|Лесі|Леся|Любов|Людмил|Марі|Марин|Мари|Наді|Наталі|Ніна|Оксан|Олен|Ольг|Світлан|Софі|Тамар|Тетян|Юлі|Александр|Алексе|Сергей|Дмитри|Евгени|Николай|Михаил|Владимир|Юри|Андре|Елен|Татьян|Наталь|Ольга|Ирин|Мари)/;
const STOP = /^(Україн|Київ|Харків|Одес|Львів|Дніпр|Росі|Москв|Верховн|Кабінет|Міністерств|Президент|Служб|Національн|Державн|Головн|Збройн|Сил|Facebook|Instagram|Telegram|TikTok|YouTube)/;
export function namesIn(text) {
  const out = new Map();
  for (const m of String(text || "").matchAll(/(?<![\p{L}])(\p{Lu}\p{Ll}{1,}(?:[-'’ʼ]\p{Lu}?\p{Ll}+)?)\s+(\p{Lu}\p{Ll}{2,})(?:\s+(\p{Lu}\p{Ll}+(?:ович|евич|йович|івна|ївна|овна|евна|ич|іч)))?(?![\p{L}])/gu)) {
    const [, a, b, c] = m;
    if (!/[а-яіїєґ]/i.test(a + b) || STOP.test(a) || STOP.test(b) || !(FIRST.test(a) || FIRST.test(b))) continue;
    const name = [a, b, c].filter(Boolean).join(" ");
    out.set(name, (out.get(name) || 0) + 1);
  }
  return [...out].sort((x, y) => y[0].split(" ").length - x[0].split(" ").length || y[1] - x[1]).slice(0, 15).map(([n]) => n);
}

export async function linkInfo(raw) {
  const u = await safeUrl(raw);
  const host = u.hostname.replace(/^www\.|^m\./, "");
  const site = /tiktok\.com$/.test(host) ? "TikTok" : /instagram\.com$/.test(host) ? "Instagram" : /(youtube\.com|youtu\.be)$/.test(host) ? "YouTube"
    : /(twitter\.com|x\.com)$/.test(host) ? "X" : /(t\.me|telegram\.me)$/.test(host) ? "Telegram" : /facebook\.com$/.test(host) ? "Facebook" : host;
  const info = { url: u.href, site, title: "", author: "", description: "", text: "", image: "", read: "", video: /tiktok|instagram\.com\/(reel|p|tv)|youtube|youtu\.be|\/video/i.test(u.href) };
  const oembed = { TikTok: "https://www.tiktok.com/oembed?url=", YouTube: "https://www.youtube.com/oembed?format=json&url=", X: "https://publish.twitter.com/oembed?omit_script=1&url=" }[site];
  if (oembed) {
    try {
      const o = await get(oembed + encodeURIComponent(u.href), { asJson: true, ms: 6000 });
      Object.assign(info, { title: o.title || "", author: o.author_name || "", image: o.thumbnail_url || "", read: "підпис і автор (oEmbed)" });
      if (site === "X" && o.html) info.text = textOf(o.html);
    } catch (e) { info.read = "oEmbed недоступний: " + e.message; }
  }
  if (!info.title || site !== "TikTok") {
    try {
      // Telegram posts: the embed view carries the text
      const page = site === "Telegram" && /^\/[\w]+\/\d+/.test(u.pathname) ? `https://t.me${u.pathname}?embed=1` : u.href;
      const html = await get(page);
      if (site === "Telegram") info.title ||= "t.me/" + u.pathname.split("/")[1];
      info.title ||= meta(html, "og:title") || ent((html.match(/<title[^>]*>([^<]*)/i) || [])[1] || "").trim();
      info.description ||= meta(html, "og:description") || meta(html, "description");
      info.author ||= meta(html, "author") || meta(html, "article:author");
      info.image ||= meta(html, "og:image");
      info.published = meta(html, "article:published_time") || "";
      const body = site === "Telegram" ? textOf((html.match(/<div class="tgme_widget_message_text[^>]*>([\s\S]*?)<\/div>/i) || [])[1] || "") : textOf(html);
      if (!info.text) info.text = body.slice(0, 8000);
      info.read ||= site === "Instagram" || site === "Facebook" ? "лише превʼю сторінки (вхід закритий для серверів)" : "текст сторінки";
    } catch (e) { info.read ||= "сторінку не вдалося прочитати: " + e.message; }
  }
  if (site === "Instagram" || site === "Facebook") info.text = "";   // a login wall, not the post
  // only names the databases know (graph names: people from posts, ЄДР roles, registries) become «find in the databases»
  // buttons: capitalised word pairs on a page are mostly menu items and places
  const cands = namesIn([info.author, info.title, info.description, info.text.slice(0, 6000)].join("\n"));
  const known = await Promise.all(cands.map((n) => query(`SELECT max(label) label, sum(n)::int n FROM search_names WHERE kind = 'person' AND (key = $1 OR key LIKE $1 || ' %')`, [n.toLowerCase()])
    .then((r) => (r[0]?.n ? { name: n, n: r[0].n } : null)).catch(() => null)));
  info.names = known.filter(Boolean);
  info.names_unchecked = cands.filter((n) => !info.names.some((k) => k.name === n)).slice(0, 4);
  return info;
}

const SYSTEM = (today) => `Ти фактчекер. Сьогодні ${today}. Тобі дають посилання і те, що вдалося прочитати зі сторінки (заголовок, підпис, текст).
1. Визнач 1–4 головні перевірювані твердження (факти, цифри, цитати, події). Думки й емоції не перевіряй.
2. Для кожного пошукай у вебі (інструмент web_search): першоджерела, офіційні дані, перевірки фактчекерів (StopFake, VoxCheck, Детектор медіа, Bellingcat, AFP, Reuters).
3. Оціни: "правда" | "неправда" | "частково" | "маніпуляція" | "не перевірити" — лише за знайденими джерелами, з посиланнями.
4. Ознаки AI-генерації (особливо відео / фото): чи платформа або автор позначає контент як AI, чи фактчекери писали, що це AI / діпфейк, чи опис містить типові ознаки. Ти НЕ бачиш саме відео: не вигадуй візуальних ознак; якщо даних немає — "не перевірити".
5. Якщо прочитано лише підпис або превʼю — так і скажи, і не роби висновку сильнішого, ніж дозволяють дані.
Відповідай ЛИШЕ JSON:
{"verdict":"правда|неправда|частково|маніпуляція|не перевірити","summary":"2-3 речення",
 "claims":[{"claim":"","verdict":"","why":"","sources":[{"title":"","url":""}]}],
 "ai":{"assessment":"позначено як AI|ймовірно AI|ознак не знайдено|не перевірити","why":""},
 "limits":"чого не вдалося перевірити"}`;

async function check(info) {
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) throw new Error("перевірка недоступна: немає ключа моделі");
  const page = `Посилання: ${info.url}\nПлатформа: ${info.site}\nПрочитано: ${info.read}\nАвтор: ${info.author}\nЗаголовок: ${info.title}\nОпис: ${info.description}\n${info.published ? "Опубліковано: " + info.published + "\n" : ""}Текст:\n${(info.text || "").slice(0, 6000)}`;
  let messages = [{ role: "user", content: page }];
  const today = new Date().toLocaleDateString("uk-UA", { timeZone: "Europe/Kyiv" });
  for (let step = 0; step < 3; step++) {
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST", signal: AbortSignal.timeout(48000),
      headers: { "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01",
        ...(process.env.ANTHROPIC_WORKSPACE_ID ? { "anthropic-workspace-id": process.env.ANTHROPIC_WORKSPACE_ID } : {}) },
      body: JSON.stringify({ model: MODEL, max_tokens: 4000, system: SYSTEM(today), messages, output_config: { effort: "low" },
        tools: [{ type: "web_search_20250305", name: "web_search", max_uses: 5 }] }),
    });
    const d = await r.json();
    if (d.error) throw new Error(d.error.message || "model error");
    if (d.stop_reason === "pause_turn") { messages = [...messages, { role: "assistant", content: d.content }]; continue; }
    const t = (d.content || []).filter((b) => b.type === "text").map((b) => b.text).join("");
    const m = t.match(/\{[\s\S]*\}/);
    if (!m) throw new Error("модель не дала відповіді");
    return JSON.parse(m[0]);
  }
  throw new Error("перевірка не завершилась");
}

export default async function handler(req, res) {
  const url = new URL(req.url, "http://x");
  const raw = (url.searchParams.get("url") || "").trim();
  if (!raw || raw.length > 2000) return json(res, 400, { error: "url required" });
  try {
    const want = url.searchParams.get("check") === "1", fresh = url.searchParams.get("fresh") === "1";
    if (want && !fresh) {
      const [c] = await query(`SELECT info, result, checked_at FROM link_checks WHERE url = $1 AND checked_at > now() - interval '24 hours'`, [raw]).catch(() => []);
      if (c) return json(res, 200, { info: c.info, result: c.result, cached: c.checked_at });
    }
    const info = await linkInfo(raw);
    if (!want) return json(res, 200, { info });
    const result = await check(info);
    await query(`INSERT INTO link_checks(url, info, result, model) VALUES ($1, $2, $3, $4) ON CONFLICT (url) DO UPDATE SET info = EXCLUDED.info, result = EXCLUDED.result, model = EXCLUDED.model, checked_at = now()`,
      [raw, JSON.stringify(info), JSON.stringify(result), MODEL]).catch(() => null);
    return json(res, 200, { info, result });
  } catch (e) {
    return json(res, 500, { error: String(e.message || e) });
  }
}
