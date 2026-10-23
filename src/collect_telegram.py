"""Keyless collector for PUBLIC Telegram channels via the web preview (t.me/s/<channel>).

Writes incremental JSONL files to data/telegram/<channel>.jsonl (deduplicated by message id).
No API keys, no login: only what any visitor can see. Be polite: small delay between requests.

Usage:
  python src/collect_telegram.py --channels kpszsu mod_russia --pages 10
  python src/collect_telegram.py --all --pages 50          # all channels in channels.json
  python src/collect_telegram.py --all --since 2025-10-17  # go back until this date (UTC)
  python src/collect_telegram.py --channels kpszsu --query "ЗБИТО/ПОДАВЛЕНО" --tag _summaries --pages 120

Limits: the preview shows ~20 posts per page; pagination goes back via ?before=<id>.
Deep history (years) is slow here; for that use Telethon with your own API keys (src/collect_telethon.py, TODO).
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "telegram"
UA = {"User-Agent": "Mozilla/5.0 (research; rhetoric-strikes)"}

MSG_RE = re.compile(r'<div class="tgme_widget_message_wrap.*?(?=<div class="tgme_widget_message_wrap|\Z)', re.S)
POST_RE = re.compile(r'data-post="([^"/]+)/(\d+)"')
TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')
VIEWS_RE = re.compile(r'<span class="tgme_widget_message_views">([^<]+)</span>')


def clean(raw: str) -> str:
    raw = re.sub(r"<br\s*/?>", "\n", raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    return html.unescape(raw).strip()


def fetch(channel: str, before: int | None = None, query: str | None = None) -> str:
    from urllib.parse import quote
    params = []
    if query:
        params.append("q=" + quote(query))
    if before:
        params.append(f"before={before}")
    url = f"https://t.me/s/{channel}" + ("?" + "&".join(params) if params else "")
    req = urllib.request.Request(url, headers=UA)
    for attempt in range(3):
        try:
            return urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:  # transient resets: retry with backoff
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def parse(page: str, channel: str):
    out = []
    for block in MSG_RE.findall(page):
        m = POST_RE.search(block)
        if not m or m.group(1).lower() != channel.lower():
            continue
        msg_id = int(m.group(2))
        t = TIME_RE.search(block)
        text_m = TEXT_RE.search(block)
        text = clean(text_m.group(1)) if text_m else ""
        views = VIEWS_RE.search(block)
        out.append({
            "id": f"tg:{channel}:{msg_id}",
            "source_id": f"tg:{channel}",
            "msg_num": msg_id,
            "ts_published": t.group(1) if t else None,
            "text": text,
            "views": views.group(1).strip() if views else None,
            "url": f"https://t.me/{channel}/{msg_id}",
            "hash": hashlib.sha256(f"{channel}|{text}".encode()).hexdigest()[:32],
        })
    return out


def load_existing(path: Path):
    ids = set()
    if path.exists():
        for line in path.open(encoding="utf-8"):
            try:
                ids.add(json.loads(line)["msg_num"])
            except Exception:  # noqa: BLE001
                pass
    return ids


def collect(channel: str, pages: int, since: datetime | None, delay: float, query: str | None = None, tag: str = "", start_before: int | None = None, match: str | None = None, out_dir: Path = OUT_DIR):
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{channel}{tag}.jsonl"
    seen = load_existing(path)
    match_re = re.compile(match, re.I | re.S) if match else None
    new = 0
    before = start_before
    last_oldest = None
    for _ in range(pages):
        try:
            page = fetch(channel, before, query)
        except Exception as e:  # noqa: BLE001
            print(f"  {channel}: fetch error {e}")
            break
        msgs = parse(page, channel)
        if not msgs:
            break
        with path.open("a", encoding="utf-8") as f:
            for m in msgs:
                if m["msg_num"] not in seen and m["text"] and (match_re is None or match_re.search(m["text"])):
                    f.write(json.dumps(m, ensure_ascii=False) + "\n")
                    seen.add(m["msg_num"])
                    new += 1
        oldest = min(m["msg_num"] for m in msgs)
        last_oldest = oldest
        oldest_ts = next((m["ts_published"] for m in msgs if m["msg_num"] == oldest), None)
        if since and oldest_ts and datetime.fromisoformat(oldest_ts.replace("Z", "+00:00")) < since:
            break
        if before is not None and oldest >= before:
            break
        before = oldest
        time.sleep(delay)
    print(f"{channel}: +{new} new, total {len(seen)}" + (f", oldest_id {last_oldest}" if last_oldest else ""))
    if match_re is not None and last_oldest:  # with a content filter the file cannot tell where paging stopped: remember it
        (out_dir / f"{channel}{tag}.cursor").write_text(str(last_oldest))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--before-id", type=int, help="start paging below this message id (resume deep backfills)")
    ap.add_argument("--since", help="YYYY-MM-DD (UTC): stop paging once older than this")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--query", help="Telegram channel search text (history backfill of specific posts)")
    ap.add_argument("--tag", default="", help="suffix for output file, e.g. _summaries")
    ap.add_argument("--match", help="regex: store only posts whose text matches (paging still walks every post)")
    ap.add_argument("--out-dir", help="output directory instead of data/telegram (e.g. data/ova: files there are parsed, not loaded into messages)")
    a = ap.parse_args()
    channels = a.channels or []
    if a.all:
        cfg = json.loads((ROOT / "channels.json").read_text(encoding="utf-8"))
        channels = [c["name"] for c in cfg["active"]]
    since = datetime.fromisoformat(a.since).replace(tzinfo=timezone.utc) if a.since else None
    for ch in channels:
        collect(ch, a.pages, since, a.delay, a.query, a.tag, a.before_id, a.match, Path(a.out_dir) if a.out_dir else OUT_DIR)
