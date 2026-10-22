-- 015: ground truth and forecasts for every oblast, not just Kharkiv (docs/ground_truth.md, "Oblast panel").
-- NOT YET APPLIED. Order: merge the code first (nowcast.py works with and without this migration), then apply this file,
-- then db/backfill/015_kharkiv_to_oblast_daily.sql, then 016_rule_geo.sql.
-- Storage: oblast_daily is ~25 regions x 1-2 summaries a day (a few thousand rows a year); everything else is views.

-- Region ids = alert-map slugs (alerts.region_id, extracted_events.region). Insert any that are missing so the FKs hold.
INSERT INTO regions(id, name, level) VALUES
 ('cherkaska','Черкаська','oblast'),('chernihivska','Чернігівська','oblast'),('chernivetska','Чернівецька','oblast'),
 ('dnipropetrovska','Дніпропетровська','oblast'),('donetska','Донецька','oblast'),('ivano-frankivska','Івано-Франківська','oblast'),
 ('kharkivska','Харківська','oblast'),('khersonska','Херсонська','oblast'),('khmelnytska','Хмельницька','oblast'),
 ('kirovohradska','Кіровоградська','oblast'),('kyiv-city','м. Київ','city'),('kyivska','Київська','oblast'),
 ('luhanska','Луганська','oblast'),('lvivska','Львівська','oblast'),('mykolaivska','Миколаївська','oblast'),
 ('odeska','Одеська','oblast'),('poltavska','Полтавська','oblast'),('rivnenska','Рівненська','oblast'),
 ('sumska','Сумська','oblast'),('ternopilska','Тернопільська','oblast'),('vinnytska','Вінницька','oblast'),
 ('volynska','Волинська','oblast'),('zakarpatska','Закарпатська','oblast'),('zaporizka','Запорізька','oblast'),
 ('zhytomyrska','Житомирська','oblast')
ON CONFLICT (id) DO NOTHING;

-- One row per oblast, day covered (Kyiv calendar day) and parsed source. Written by src/load_to_db.py oblast.
--   source  'ova_summary' = daily summary of the oblast administration (src/parse_oblast_daily.py; Kharkiv rows also
--           backfilled from kharkiv_daily). Other parsed sources can be added under their own name.
--   complete = the summary lists every weapon class with a number, so NULL/0 means zero. Only complete rows can make a
--           NEGATIVE target day; incomplete ones only confirm positives (see oblast_daily_gt).
--   drones = strike drones (Shahed/Geran/"тип встановлюється"), FPV kept apart in fpv; kab = guided bombs / air strikes.
CREATE TABLE IF NOT EXISTS oblast_daily (
  region_id     text NOT NULL REFERENCES regions(id),
  date          date NOT NULL,
  source        text NOT NULL,
  missiles      int, drones int, fpv int, kab int, mlrs int, artillery int,
  attacks_n     int,             -- "N ударів по M населених пунктах"
  settlements_n int,
  shot_down     int,             -- interceptions claimed over the oblast (exposure, not hits)
  killed        int, injured int,
  attacked      boolean NOT NULL DEFAULT false,
  city_attacked boolean,         -- oblast centre named as hit (Kharkiv parser only so far)
  complete      boolean NOT NULL DEFAULT false,
  msg_ids       text[] NOT NULL DEFAULT '{}',
  url           text,
  parser        text,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (region_id, date, source)
);
CREATE INDEX IF NOT EXISTS oblast_daily_date_idx ON oblast_daily (date);

-- Air-raid alert exposure per oblast and Kyiv day: count and minutes of oblast-level alerts. Proxy for threat
-- presence only (docs/ground_truth.md: alerts sound almost daily, ~0 correlation with intensity) -> feature, never label.
-- Volunteer and official data overlap: take the source with more minutes that day instead of adding them.
CREATE OR REPLACE VIEW oblast_alerts_daily AS
WITH a AS (
  SELECT region_id, (ts_start AT TIME ZONE 'Europe/Kyiv')::date AS date, source,
         count(*) AS n, sum(extract(epoch FROM least(coalesce(ts_end, ts_start + interval '6 hours'), ts_start + interval '24 hours') - ts_start) / 60.0) AS minutes
  FROM alerts WHERE coalesce(level, 'oblast') = 'oblast' AND alert_type = 'air_raid'
  GROUP BY 1, 2, 3)
SELECT DISTINCT ON (region_id, date) region_id, date, n::int AS alert_n, round(minutes)::int AS alert_min, source AS alert_source
FROM a ORDER BY region_id, date, minutes DESC;

-- How much of day D the statements/events extractor has read: extracted_events can only give NEGATIVE days when the
-- extractor saw that day's news. Threshold lives in the model spec (ee_min_extracted), not here.
CREATE OR REPLACE VIEW extraction_coverage_daily AS
SELECT (m.ts_published AT TIME ZONE 'Europe/Kyiv')::date AS date, count(*)::int AS n_extracted
FROM statement_extractions x JOIN messages m ON m.id = x.message_id
GROUP BY 1;

-- Ground truth per oblast and day, all sources side by side, one row. Counts = max over sources (sources repeat the same
-- figures). complete = any complete OVA summary that day; ee_* = LLM-extracted strike events (positive evidence only).
CREATE OR REPLACE VIEW oblast_daily_gt AS
WITH ova AS (
  SELECT region_id, date, max(missiles) AS missiles, max(drones) AS drones, max(fpv) AS fpv, max(kab) AS kab,
         bool_or(attacked) AS attacked, bool_or(complete) AS complete, array_agg(DISTINCT source) AS sources
  FROM oblast_daily GROUP BY 1, 2),
ee AS (
  SELECT region AS region_id, date, missiles, drones, guided_bombs AS kab, n_reports
  FROM region_daily_strikes WHERE region IN (SELECT id FROM regions)),
k AS (SELECT region_id, date FROM ova UNION SELECT region_id, date FROM ee UNION SELECT region_id, date FROM oblast_alerts_daily)
SELECT k.region_id, k.date,
       greatest(ova.missiles, ee.missiles) AS missiles,
       greatest(ova.drones, ee.drones) AS drones,
       greatest(ova.kab, ee.kab) AS kab,
       ova.fpv,
       coalesce(ova.attacked, false) OR coalesce(ee.missiles + ee.drones + ee.kab > 0, false) AS attacked,
       coalesce(ova.complete, false) AS complete,
       ee.n_reports AS ee_reports,
       al.alert_n, al.alert_min,
       coalesce(ova.sources, '{}') || CASE WHEN ee.region_id IS NOT NULL THEN ARRAY['extracted_events'] ELSE '{}'::text[] END AS sources
FROM k
LEFT JOIN ova ON ova.region_id = k.region_id AND ova.date = k.date
LEFT JOIN ee ON ee.region_id = k.region_id AND ee.date = k.date
LEFT JOIN oblast_alerts_daily al ON al.region_id = k.region_id AND al.date = k.date;

-- Forecasts get a region: one row per model, region and target day. Single-region models keep working:
-- Kharkiv models -> 'kharkivska', national models -> 'ukraine' (pseudo-region, not an FK).
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS region_id text NOT NULL DEFAULT 'ukraine';
UPDATE predictions SET region_id = 'kharkivska' WHERE model LIKE 'kh\_%' AND region_id = 'ukraine';
ALTER TABLE predictions DROP CONSTRAINT IF EXISTS predictions_pkey;
ALTER TABLE predictions ADD PRIMARY KEY (model, region_id, target_date);
