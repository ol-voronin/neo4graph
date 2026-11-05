# rhetoric-strikes (neo4graph) — інструкції для Claude Code

Дослідницький прототип: чи передує зсув риторики (Telegram, заяви, медіа) масованим ударам по містах України. Це не система раннього попередження. Деталі й етика — `README.md`; архітектура і стан — `docs/architecture.md`.

## Де що
- `src/` — Python-пайплайни (збір, розмітка LLM, ознаки, прогноз). `models/` — моделі. `db/schema.sql`, `db/migrations/` — Postgres на Neon (PG17, pgvector).
- `web/` — сайт rhetoric-graph.vercel.app: статичні сторінки + `web/api/*.js` (Neon serverless), регіон `fra1`, кеш за `web/vercel.json`.
- `.github/workflows/` — планові збори й прогнози (секрети репозиторію). `deploy/vps.md` — VPS-воркер з українським IP (systemd-таймери); секрети лише в `/etc/neo4graph.env` і в Vercel/GitHub, у репозиторії їх немає.

## Команди
```bash
pip install -r requirements.txt           # Python-частина
python src/test_oblast_pipeline.py        # синтетична перевірка пайплайну без бази
cd web && npm install                     # сайт; локально: npx vercel dev (потрібен DATABASE_URL)
```
Окремого тестового набору для `web/api` немає: перевіряй ендпоінт реальним запитом до превʼю-адреси Vercel.

## Правила
- Лише публічні джерела. Антикорупційні справи зберігаються як *статус* (підозра / обвинувачення / вирок), ніколи як вина.
- Нічого не вигадувати: цифри, пороги, назви каналів — з коду, бази або документа з датою.
- Міграції бази (`db/migrations/`) — файлом, запуск на Neon лише після «так» власника. `.env` не читати й не редагувати.
- Прод (`main`) деплоїться Vercel автоматично, тож push у `main` і merge PR — лише після «так».

## Цикл роботи
`/sdlc-spec` → `/sdlc-ship` → `/sdlc-release`. Агенти й скіли `sdlc-*` у `.claude/` згенеровані з репо office (`scripts/sync-sdlc.mjs`): тут їх не редагувати. Для доменних питань даних і ризиків — агент `researcher`; для `web/api` — `backend-dev`; для сторінок — `frontend-dev`.
