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

GitHub keeps what needs the repository secrets: the frozen daily forecast (nowcast.yml), the registry queue's cloud
leg (queue.yml, started by the live.yml watcher whenever queries wait), lake, history backfills. collect.yml is manual only.

## Everyday checks
    ssh root@185.233.44.248 systemctl list-timers 'neo4graph-*'
    ssh root@185.233.44.248 journalctl -u neo4graph-uaworker -n 50
    ssh root@185.233.44.248 journalctl -u neo4graph-collect --since -1h | tail

## The old Mac worker
`com.rhetoric.uaworker` (launchd, ~/Claude/registry) is disabled so two workers don't take the same items. To bring it back
(e.g. the VPS is down): `launchctl enable gui/$(id -u)/com.rhetoric.uaworker && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rhetoric.uaworker.plist`.
