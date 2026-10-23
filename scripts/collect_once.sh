#!/usr/bin/env bash
# One collection pass (shared by .github/workflows/collect.yml and live.yml): Telegram + RSS + Air Force / Kharkiv / oblast
# summaries, then load into Neon. PAGES (default 3) = t.me/s pages per channel. Needs DATABASE_URL for the load.
set -u
PAGES="${PAGES:-3}"
python src/collect_telegram.py --all --pages "$PAGES" --delay 1.0
python src/collect_rss.py --all
python src/collect_telegram.py --channels kpszsu --query "ЗБИТО/ПОДАВЛЕНО" --tag _summaries --pages 3 --delay 1.0
python src/parse_kpszsu.py
python src/parse_kharkiv_daily.py
# oblast administration summaries into data/ova (runner disk only): parsed into oblast_daily, never loaded into messages
python src/collect_telegram.py --channels $(python src/oblasts.py --handles) --out-dir data/ova --pages 3 --delay 1.0
python src/parse_oblast_daily.py
if [ -n "${DATABASE_URL:-}" ]; then
  python src/load_to_db.py messages
  python src/load_to_db.py waves
  python src/load_to_db.py kharkiv
  python src/load_to_db.py oblast   # skips with a message until migration 015 is applied
else
  echo "DATABASE_URL is not set: skipping DB load"
fi
