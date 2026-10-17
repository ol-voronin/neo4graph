-- Rule-based baseline features (free, no LLM). Runs inside Postgres on the same messages as the LLM labels,
-- so rules / Gemini / manual gold can be compared row by row. Keyword lists are ru/uk/en and deliberately simple.
create or replace view rule_features as
with t as (select id, source_id, ts_published, lower(text) as x from messages)
select id as message_id, source_id, ts_published,
  (x ~ '(удар|ракет|обстріл|обстрел|знищ|уничтож|ядерн|орешник|орєшнік|oreshnik|масован|массирован|відплат|возмезд|ответн|ультиматум|retaliat|strike|missile|nuclear|annihilat|сотр[её]м|стерти)') as r_threat,
  (x ~ '(харк|kharkiv|харьков|київ|киев|kyiv|одес|odes|дніпр|днепр|львів|львов|запор|енергет|энергет|infrastructure|інфраструктур|инфраструктур|decision.?making cent|центр.{0,10}(прийняття|принятия))') as r_target,
  (x ~ '(сьогодні|сегодня|завтра|найближч|ближайш|на цьому тижні|на этой неделе|скоро|невдовзі|tonight|tomorrow|within|soon|in the coming|через \d+|протягом \d+|в течение \d+)') as r_time,
  (x ~ '(переговор|саміт|саммит|summit|talks|годовщин|вибори|выборы|election|парад|мюнхен|munich|рада безпеки|совбез)') as r_scheduled,
  (x ~ '(повітряна тривога|воздушная тревога|пуск|бпла|шахед|shahed|ракета з|курс на|напрямку|направлении)') as r_operational
from t;

create or replace view rule_labels as
select *, case
  when r_operational and not r_threat then 0
  when r_threat and r_target and r_time then 3
  when r_threat and (r_target or r_time) then 2
  when r_threat then 1 else 0 end as rule_threat_level
from rule_features;
