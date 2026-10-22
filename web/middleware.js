// Private site: every page and API answers 401 unless the browser holds the site key.
// Unlock a device once with /unlock?key=<SITE_KEY> (cookie for a year). /api/ingest keeps its own token.
// Runs before the CDN cache, so cached API responses are not served to locked browsers either.
const COOKIE = "site_key";
const YEAR = 365 * 24 * 3600;

async function digest(s) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode("rhetoric-graph:" + s));
  return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
}

const pass = () => new Response(null, { headers: { "x-middleware-next": "1" } });

const LOCKED = `<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Доступ закрито</title>
<body style="font:16px system-ui;display:grid;place-items:center;min-height:90vh;margin:0;padding:16px;background:#111;color:#ddd">
<p>Доступ закрито.</p></body></html>`;

export default async function middleware(request) {
  const key = process.env.SITE_KEY;
  if (!key) return pass();  // not configured yet: open (set SITE_KEY on Vercel to lock)
  const url = new URL(request.url);
  if (url.pathname === "/api/ingest") return pass();
  const want = await digest(key);

  if (url.pathname === "/unlock") {
    if (url.searchParams.get("key") !== key) return new Response(LOCKED, { status: 401, headers: { "content-type": "text/html; charset=utf-8" } });
    return new Response(null, { status: 302, headers: {
      location: "/", "set-cookie": `${COOKIE}=${want}; Path=/; Max-Age=${YEAR}; HttpOnly; Secure; SameSite=Lax`, "cache-control": "no-store" } });
  }

  const got = (request.headers.get("cookie") || "").split(/;\s*/).find((c) => c.startsWith(COOKIE + "="))?.slice(COOKIE.length + 1);
  if (got === want || request.headers.get("x-site-key") === key) return pass();
  const api = url.pathname.startsWith("/api/");
  return new Response(api ? JSON.stringify({ error: "доступ закрито" }) : LOCKED, { status: 401, headers: {
    "content-type": api ? "application/json; charset=utf-8" : "text/html; charset=utf-8",
    "x-robots-tag": "noindex", "cache-control": "no-store" } });
}

export const config = { runtime: "nodejs" };
