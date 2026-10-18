-- Statements / actions / regional strike events (docs/statements_layer.md). `events` already holds the holiday calendar, hence extracted_events.
CREATE TABLE IF NOT EXISTS statements (
    id              bigserial PRIMARY KEY,
    message_id      text NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    ts_published    timestamptz NOT NULL,
    actor_side      text NOT NULL,
    actor_name      text,
    stype           text NOT NULL,
    quote           text NOT NULL,
    predicted_event text,
    target_region   text,
    horizon_days    int NOT NULL DEFAULT 30,
    resolvable      boolean NOT NULL DEFAULT false,
    status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','true','false','partial','unresolvable')),
    resolved_at     timestamptz,
    evidence_ids    text[] NOT NULL DEFAULT '{}',
    resolver_note   text,
    model           text NOT NULL,
    prompt_version  text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS statements_msg_idx ON statements(message_id);
CREATE INDEX IF NOT EXISTS statements_due_idx ON statements(status, ts_published) WHERE resolvable;

CREATE TABLE IF NOT EXISTS extracted_events (
    id              bigserial PRIMARY KEY,
    message_id      text NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    event_type      text NOT NULL,
    event_date      date,
    region          text,
    actor           text,
    missiles        int NOT NULL DEFAULT 0,
    drones          int NOT NULL DEFAULT 0,
    guided_bombs    int NOT NULL DEFAULT 0,
    killed          int NOT NULL DEFAULT 0,
    injured         int NOT NULL DEFAULT 0,
    details         text,
    confidence      real NOT NULL DEFAULT 0.5,
    model           text NOT NULL,
    prompt_version  text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_date_region_idx ON extracted_events(event_date, region);
CREATE INDEX IF NOT EXISTS events_msg_idx ON extracted_events(message_id);

-- Which messages have been through the statements/events extractor (even if nothing was found).
CREATE TABLE IF NOT EXISTS statement_extractions (
    message_id      text PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    n_statements    int NOT NULL,
    n_events        int NOT NULL,
    model           text NOT NULL,
    prompt_version  text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Track record per actor: share of resolved statements that came true (partial = 0.5), with counts.
CREATE OR REPLACE VIEW actor_track_record AS
SELECT actor_side, stype,
       count(*) FILTER (WHERE status IN ('true','false','partial')) AS n_resolved,
       count(*) FILTER (WHERE status = 'pending' AND resolvable) AS n_pending,
       round((sum(CASE status WHEN 'true' THEN 1 WHEN 'partial' THEN 0.5 ELSE 0 END)
              / NULLIF(count(*) FILTER (WHERE status IN ('true','false','partial')), 0))::numeric, 3) AS pct_true
FROM statements GROUP BY actor_side, stype;

-- Regional strike panel: per day and oblast, max over sources (sources repeat the same figures; max avoids double counting).
CREATE OR REPLACE VIEW region_daily_strikes AS
SELECT event_date AS date, region,
       max(missiles) AS missiles, max(drones) AS drones, max(guided_bombs) AS guided_bombs,
       max(killed) AS killed, max(injured) AS injured, count(*) AS n_reports, max(confidence) AS confidence
FROM extracted_events WHERE event_type = 'strike' AND event_date IS NOT NULL AND region IS NOT NULL
GROUP BY event_date, region;
