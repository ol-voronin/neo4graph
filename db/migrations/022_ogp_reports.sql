-- 022: Office of the Prosecutor General report forms (src/ogp_reports.py, docs/ogp_reports.md). National figures only.
-- Idempotent. Apply: gh workflow run migrate -f files="db/migrations/022_ogp_reports.sql"

-- One value per form, reporting period, key and measure. Periods are cumulative from January (period_from = January 1st)
-- except the 2018–2019 quarterly 1-ЛФТ files, whose period_from = period_to as published.
-- key: total | sections_published | sec:<section> | art:<article> | detected_by:<agency> | money:<sum> | oz:<row>
-- measure: registered | suspicion (forma_1); persons_suspected | persons_identified (forma_2); value (money, forma_1_oz);
--          registered | investigated | started | persons_identified (forma_1_lft)
CREATE TABLE IF NOT EXISTS ogp_stats (
    form        text NOT NULL,
    period_from date NOT NULL,
    period_to   date NOT NULL,
    key         text NOT NULL,
    measure     text NOT NULL,
    value       double precision NOT NULL,
    PRIMARY KEY (form, key, measure, period_from, period_to)
);
-- the row label as published (one per key; kept apart so the long Ukrainian labels are not repeated per period)
CREATE TABLE IF NOT EXISTS ogp_keys (
    form  text NOT NULL,
    key   text NOT NULL,
    label text,
    PRIMARY KEY (form, key)
);

-- Year-to-date values, plus the military-crimes section (розділ XIX: ст. 402–435, incl. 407 СЗЧ and 408 дезертирство)
-- for the periods where it is not published (since 2022): total minus the sum of the published sections. In 2018–2021,
-- where every section is published, that difference is exactly 0, which is what makes the derived figure trustworthy.
CREATE OR REPLACE VIEW ogp_ytd AS
SELECT form, period_to, key, measure, value, false AS derived
FROM ogp_stats WHERE extract(month FROM period_from) = 1
UNION ALL
SELECT 'forma_1', t.period_to, 'sec:military', t.measure, t.value - s.value, true
FROM ogp_stats t
JOIN ogp_stats s ON s.form = 'forma_1' AND s.key = 'sections_published' AND s.measure = t.measure
                AND s.period_from = t.period_from AND s.period_to = t.period_to
WHERE t.form = 'forma_1' AND t.key = 'total' AND extract(month FROM t.period_from) = 1
  AND NOT EXISTS (SELECT 1 FROM ogp_stats m WHERE m.form = 'forma_1' AND m.key = 'sec:military' AND m.measure = t.measure
                  AND m.period_from = t.period_from AND m.period_to = t.period_to);

-- Increment over the previous report of the same year: months = how many months it covers (1 normally; more where a
-- monthly report is missing); a January report or the first one of a year counts from January.
CREATE OR REPLACE VIEW ogp_monthly AS
SELECT form, key, measure, period_to AS month, derived,
       value - coalesce(lag(value) OVER w, 0) AS value,
       extract(month FROM period_to)::int - coalesce(extract(month FROM lag(period_to) OVER w)::int, 0) AS months
FROM ogp_ytd
WINDOW w AS (PARTITION BY form, key, measure, extract(year FROM period_to) ORDER BY period_to);
