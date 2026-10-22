-- One-off, idempotent: copy the Kharkiv governor summaries (kharkiv_daily, parse_kharkiv_daily.py) into oblast_daily.
-- Run after db/migrations/015_oblast_daily.sql. kharkiv_daily stays and keeps feeding kh_missile_v1 / kh_missile_base_v1
-- until the pooled oblast model is validated; collect.yml now writes both.
-- complete = true: the governor's weapon block lists every class; a row without the block (has_weapon_block was not
-- stored in kharkiv_daily) has all counts NULL/0 and settlements_n NULL, and is marked incomplete here.
INSERT INTO oblast_daily (region_id, date, source, missiles, drones, fpv, kab, mlrs, settlements_n,
                          attacked, city_attacked, complete, msg_ids, url, parser)
SELECT 'kharkivska', date, 'ova_summary', missiles, drones, fpv, guided_bombs, mlrs, settlements_n,
       coalesce(missiles, 0) + coalesce(drones, 0) + coalesce(fpv, 0) + coalesce(guided_bombs, 0) + coalesce(mlrs, 0) > 0
         OR coalesce(city_attacked, false) OR coalesce(settlements_n, 0) > 0,
       city_attacked,
       coalesce(missiles, 0) + coalesce(drones, 0) + coalesce(fpv, 0) + coalesce(guided_bombs, 0) + coalesce(mlrs, 0) > 0
         OR settlements_n IS NOT NULL,
       CASE WHEN msg_id IS NULL THEN '{}'::text[] ELSE ARRAY[msg_id] END, url, 'parse_kharkiv_daily (backfill 015)'
FROM kharkiv_daily
ON CONFLICT (region_id, date, source) DO NOTHING;

-- Check: the two tables must agree on every Kharkiv day (expect 0 rows).
SELECT k.date, k.missiles, o.missiles, k.drones, o.drones
FROM kharkiv_daily k JOIN oblast_daily o ON o.region_id = 'kharkivska' AND o.date = k.date AND o.source = 'ova_summary'
WHERE k.missiles IS DISTINCT FROM o.missiles OR k.drones IS DISTINCT FROM o.drones;
