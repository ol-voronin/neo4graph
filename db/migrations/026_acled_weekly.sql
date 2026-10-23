-- 026: ACLED weekly aggregates (myACLED "Aggregated data" export: week x admin1 x sub_event_type). Loaded by
-- src/acled.py from a CSV the owner downloads; the data is licensed (no redistribution), so it lives here, never in the
-- public repo. Used by the oblast model v3 (features.acled_weekly): per-oblast strike rates and weeks with no strike
-- event at all as known negatives. Idempotent.

CREATE TABLE IF NOT EXISTS acled_weekly (
    week            date NOT NULL,       -- first day of the ACLED week (a Saturday); the week is week .. week + 6
    country         text NOT NULL,
    admin1          text NOT NULL,       -- ACLED name ('Kyiv City', 'Zaporizhia', ...)
    region_id       text,                -- our oblast slug (src/acled.py ADMIN1), NULL outside Ukraine
    event_type      text NOT NULL,
    sub_event_type  text NOT NULL,
    events          integer NOT NULL,
    fatalities      integer NOT NULL DEFAULT 0,
    population_exposure bigint,
    disorder_type   text,
    loaded_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (week, country, admin1, sub_event_type)
);
CREATE INDEX IF NOT EXISTS acled_weekly_region_idx ON acled_weekly (region_id, week);
