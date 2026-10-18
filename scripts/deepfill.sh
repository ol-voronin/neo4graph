#!/usr/bin/env bash
# Deep history backfill for Telegram channels (t.me/s resets the connection after ~400-500 posts, so page in resumable chunks).
# Usage: scripts/deepfill.sh "<channel> <channel> ..." 2026-04-01
set -u
CHANNELS="$1"; SINCE="$2"
for ch in $CHANNELS; do
  F="data/telegram/${ch}.jsonl"
  for i in $(seq 1 14); do
    BEFORE=""; OLDEST="none"
    if [ -s "$F" ]; then
      BEFORE="--before-id $(python3 -c "import json;print(min(json.loads(l)['msg_num'] for l in open('$F')))")"
      OLDEST=$(python3 -c "import json;print(min(json.loads(l)['ts_published'] for l in open('$F'))[:10])")
      # resume below the oldest only after the first chunk; the first chunk starts from the newest post
    fi
    if [ "$OLDEST" != "none" ] && [[ "$OLDEST" < "$SINCE" ]]; then echo "$ch: reached $OLDEST"; break; fi
    echo "$ch chunk $i oldest=$OLDEST"
    python3 src/collect_telegram.py --channels "$ch" --pages 400 --since "$SINCE" --delay 0.7 $BEFORE 2>&1 | tail -1
    sleep 30
  done
done
