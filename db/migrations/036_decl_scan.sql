-- Judges whose NAZK declarations the VPS fetched ahead of time (local/decl_judges.py): one row per name as decisions write it
-- («Прізвище І. Б.»); re-checked after 30 days. api/queue.js skips a kind=decl request for a name scanned here.
CREATE TABLE IF NOT EXISTS decl_scan (
  name text PRIMARY KEY, court text, found int, note text, done_at timestamptz NOT NULL DEFAULT now());
