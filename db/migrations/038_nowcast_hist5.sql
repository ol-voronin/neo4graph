-- 038: daily aggregates for the oblast model v5 (docs/forecast_v5.md), built by `python src/hist5.py build` from the
-- history Parquets (panel_posts.parquet, monitors/monitor_posts.parquet) so that the forecast job (Neon only) can train
-- on 2022-2026. Same columns as the live SQL of features.rhetoric() / rhetoric_geo() and monitors.daily(). d = target
-- day (the 24 h feature window ends at cutoff_hour on d-1). Idempotent.
CREATE TABLE IF NOT EXISTS rhetoric_daily_hist5 (
  cutoff_hour smallint NOT NULL, d date NOT NULL,
  posts_ru int, thr_ru real, l2_ru real, tgt_ru real,
  tc_energy_ru real, tc_dc_ru real, tc_rail_ru real, tc_vpk_ru real,
  built_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (cutoff_hour, d));

CREATE TABLE IF NOT EXISTS rhetoric_region_hist5 (
  cutoff_hour smallint NOT NULL, d date NOT NULL, region_id text NOT NULL,
  n_thr_geo_ru int NOT NULL, n_tcg_ru int NOT NULL,
  PRIMARY KEY (cutoff_hour, d, region_id));

CREATE TABLE IF NOT EXISTS monitor_daily_hist5 (
  cutoff_hour smallint NOT NULL, d date NOT NULL,
  mon_posts int NOT NULL, mon_strat int NOT NULL, mon_sea int NOT NULL, mon_warn int NOT NULL,
  built_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (cutoff_hour, d));

CREATE TABLE IF NOT EXISTS monitor_region_hist5 (
  cutoff_hour smallint NOT NULL, d date NOT NULL, region_id text NOT NULL,
  mon_reg int NOT NULL,
  PRIMARY KEY (cutoff_hour, d, region_id));
