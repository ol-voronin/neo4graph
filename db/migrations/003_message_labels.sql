-- 2026-10-17: raw LLM output per message (keeps anticorruption `case` block, entities, narrative label, versions)
CREATE TABLE IF NOT EXISTS message_labels (
  message_id text PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  prompt_version text NOT NULL, model text NOT NULL,
  raw jsonb NOT NULL, labeled_at timestamptz DEFAULT now()
);
