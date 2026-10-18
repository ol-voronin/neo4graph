#!/usr/bin/env bash
# Deep backfill with a content filter (stores only matching posts; pages through everything in resumable chunks).
# Usage: scripts/deepfill_match.sh kpszsu _summaries 'ЗБИТО/ПОДАВЛЕНО|У ніч на' 2026-03-31
set -u
CH="$1"; TAG="$2"; MATCH="$3"; SINCE="$4"; CUR="data/telegram/${CH}${TAG}.cursor"
for i in $(seq 1 40); do
  BEFORE=""; [ -s "$CUR" ] && BEFORE="--before-id $(cat "$CUR")"
  OUT=$(python3 src/collect_telegram.py --channels "$CH" --pages 120 --since "$SINCE" --delay 0.7 --tag "$TAG" --match "$MATCH" $BEFORE 2>&1 | tail -1)
  echo "chunk $i: $OUT"
  F="data/telegram/${CH}${TAG}.jsonl"
  if [ -s "$F" ]; then
    OLDEST=$(python3 -c "import json;print(min(json.loads(l)['ts_published'] for l in open('$F'))[:10])")
    if [[ "$OLDEST" < "$SINCE" ]]; then echo "reached $OLDEST"; break; fi
  fi
  echo "$OUT" | grep -q "oldest_id" || break
  sleep 20
done
