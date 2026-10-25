#!/bin/bash
# VPS: the cloud leg of the registry queue (deploy/vps.md, neo4graph-drain.service). Takes one queued item at a time as
# soon as it appears instead of waiting for a GitHub runner (3-5 min) and keeps the court dumps indexed on disk
# (EDRSR_CACHE=1, src/edrsr_index.py). Waits until the search / model keys are in /etc/neo4graph.env: without them a
# chain would finish without its web and LLM steps, and GitHub (queue.yml) keeps draining meanwhile.
cd /opt/neo4graph || exit 1
export EDRSR_CACHE=1
while true; do
  if [ -z "${ANTHROPIC_API_KEY:-}" ] || [ -z "${TAVILY_API_KEY:-}${BRAVE_API_KEY:-}${GOOGLE_CSE_KEY:-}" ]; then
    echo "keys missing in /etc/neo4graph.env (ANTHROPIC_API_KEY + a search key); waiting"; sleep 600; exit 0
  fi
  python -u src/drain_queue.py --max 1 | tail -n 40
  sleep 10
done
