# VPS worker (since 2026-10-23)

ukraine.com.ua «VPS 6G» (2 vCPU, 6 GB, 50 GB NVMe, Kyiv), Ubuntu 24.04, host `vps-61489`, IP 185.233.44.248 (geo: Kyiv, UA).
Why: a Ukrainian IP for the registry worker (the cloud cannot reach reyestr.court.gov.ua, NAZK, ...) and collection every
~20 min instead of GitHub's throttled crons. The database stays on Neon, the site on Vercel.

## Access and hardening
- SSH key only (`/etc/ssh/sshd_config.d/00-hardening.conf`: no passwords, root by key), ufw allows OpenSSH only,
  fail2ban, unattended-upgrades, timezone Europe/Kyiv.
- Code: `/opt/neo4graph` (user `neo4graph`, clone of main, venv `.venv`), updated daily by `neo4graph-update.timer`.
- Secrets: `/etc/neo4graph.env` (root:neo4graph 640): `DATABASE_URL`, `API_BASE`, `INGEST_TOKEN`. Not in the repo.

## Services (systemd)
| unit | what | when |
|---|---|---|
| `neo4graph-uaworker.service` | `local/ua_worker.py --loop 60`: UA leg of registry queries (court register, NAZK declarations, ФОП, debtors) | always on |
| `neo4graph-collect.timer` → `.service` | `scripts/collect_once.sh`: Telegram, RSS, Air Force / oblast summaries → Neon | 10 min after the previous pass ends (~20 min cycle) |
| `neo4graph-live.timer` → `.service` | `src/nowcast.py live` + `src/explosions.py build` | :05 and :35 |
| `neo4graph-update.timer` | `git pull --ff-only` + `pip install -r requirements.txt` | daily 04:20 |
| `neo4graph-drain.service` | `scripts/drain_loop.sh`: the cloud leg of the registry queue (web search, LLM, ЄДР, ЄДРСР) — takes an item within ~10 s; waits until the keys are set | always on |
| `neo4graph-edrsr.timer` → `.service` | `src/edrsr_index.py --years <this year> --build-index --refresh`: re-downloads the current year's court dump and rebuilds its parquet index (older years are indexed once) | daily 03:40 |
| `neo4graph-nightly.timer` → `.service` | `src/person_cases.py build`: lawyers named in decision texts → person_case_links (fresh lawyers.parquet from the lake release) | daily 05:10 |

Keys for the drain (in `/etc/neo4graph.env`, set by the owner): `ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID`, `TAVILY_API_KEY` (and/or `BRAVE_API_KEY`, `GOOGLE_CSE_KEY` + `GOOGLE_CSE_ID`), `GEMINI_API_KEY`. Court dumps and indexes: `/opt/neo4graph/data/edrsr` (~10 GB).

GitHub keeps what needs the repository secrets: the frozen daily forecast (nowcast.yml), the registry queue's cloud
leg (queue.yml, started by the live.yml watcher whenever queries wait), lake, history backfills. collect.yml is manual only.

## Everyday checks
    ssh root@185.233.44.248 systemctl list-timers 'neo4graph-*'
    ssh root@185.233.44.248 journalctl -u neo4graph-uaworker -n 50
    ssh root@185.233.44.248 journalctl -u neo4graph-collect --since -1h | tail

## The old Mac worker
`com.rhetoric.uaworker` (launchd, ~/Claude/registry) is disabled so two workers don't take the same items. To bring it back
(e.g. the VPS is down): `launchctl enable gui/$(id -u)/com.rhetoric.uaworker && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rhetoric.uaworker.plist`.
