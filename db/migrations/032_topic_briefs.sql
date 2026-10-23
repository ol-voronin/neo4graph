-- Topic context on the fact-check page (src/topic_briefs.py, daily): what is going on in a topic over the last 30 days,
-- written by the model from our own statements and extracted events only; every item cites ids from that data.
-- topic '' = all topics.
CREATE TABLE IF NOT EXISTS topic_briefs (
    topic         text PRIMARY KEY,
    generated_at  timestamptz NOT NULL DEFAULT now(),
    days          int NOT NULL,
    body          jsonb NOT NULL,     -- {summary, events:[{date,text,ref}], sides:{UA,RU,other}, checked:[{ref,text}], watch}
    stats         jsonb NOT NULL,     -- {n, n_prev, actors:[{name,n}], verdicts:{t,f,p,u}}
    refs          jsonb NOT NULL,     -- ref -> {url, date, source} for the links
    model         text NOT NULL
);
