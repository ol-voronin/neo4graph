-- Daily rhetoric feature series for lead-lag tests. Rule features cover ALL posts (free, full history);
-- LLM features (threat_level etc.) exist only for the labeled subset, so they are reported with their coverage.
-- Day = Kyiv-time calendar day of publication. Official/milblogger/media sources only (alert/OSINT feeds excluded).
create or replace view daily_features as
with base as (
  select (m.ts_published at time zone 'Europe/Kyiv')::date as day, m.id, s.country, s.type,
         r.r_threat, r.r_target, r.r_time, r.rule_threat_level,
         m.threat_level, (m.labeled_at is not null) as labeled
  from messages m join sources s on s.id = m.source_id join rule_labels r on r.message_id = m.id
  where s.type <> 'osint' and length(m.text) >= 40
)
select day,
  count(*) as posts,
  count(*) filter (where country='RU') as posts_ru,
  count(*) filter (where country='UA') as posts_ua,
  avg(r_threat::int)::float as rule_threat_share,
  avg((rule_threat_level >= 2)::int)::float as rule_l2_share,
  avg((rule_threat_level >= 2)::int) filter (where country='RU')::float as rule_l2_share_ru,
  avg((rule_threat_level >= 2)::int) filter (where country='UA')::float as rule_l2_share_ua,
  count(*) filter (where labeled) as labeled_posts,
  avg((threat_level >= 1)::int) filter (where labeled)::float as llm_threat_share
from base group by day;
