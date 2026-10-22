-- Daily panel rhetoric aggregates before the live panel start (2026-03-31), built by src/history.py from raw posts kept
-- in the GitHub release `history-latest` (not in Neon). Same windows and rules as features.rhetoric(); panel-only counts.
CREATE TABLE IF NOT EXISTS rhetoric_daily_hist (
  cutoff_hour smallint NOT NULL, d date NOT NULL,
  posts_p int, posts_ru int, thr_ru real, l2_ru real, tgt_ru real, thr_ua real, l2_ua real,
  dip_p int, dip_ua_p int, peace_p int, built_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (cutoff_hour, d));
