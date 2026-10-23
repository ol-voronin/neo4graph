-- 028: weekly RU panel rhetoric per oblast (src/history.py weekly) for the weekly oblast test (src/weekly.py), from the
-- history Parquet (release history-latest, 2022 onwards) and the live panel. `week` is the TARGET ACLED week (Saturday
-- start): the posts are from the week before it. Rebuilt in full by `history.py weekly`. Idempotent.
CREATE TABLE IF NOT EXISTS rhetoric_weekly (
  week date NOT NULL, region_id text NOT NULL,
  posts_ru int, thr_ru real, l2_ru real, geo_ru real, geo_thr_ru real, geo_tcg_ru real,
  built_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (week, region_id));
