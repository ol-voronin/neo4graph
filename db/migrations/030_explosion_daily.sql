-- 030: explosions reported by the monitoring channels, per oblast and Kyiv day (src/explosions.py): reports = posts saying
-- explosions happened that name the oblast; known = a report, or the feed was alive (>= 30 monitor posts) that day and
-- the oblast is not occupied. Target of the oblast model v4 ("explosions in the oblast tomorrow"). Rebuilt by
-- `explosions.py build` from the history Parquet + live monitor posts. Idempotent.
CREATE TABLE IF NOT EXISTS explosion_daily (
  region_id text NOT NULL, d date NOT NULL, reports int NOT NULL, posts int NOT NULL, known boolean NOT NULL,
  PRIMARY KEY (region_id, d));
