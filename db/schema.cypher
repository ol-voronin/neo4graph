// =====================================================
// Neo4j schema: rhetoric -> strike prediction project
// Neo4j 5.x. Все времена хранить в UTC (datetime).
// =====================================================

// ---------- CONSTRAINTS ----------
CREATE CONSTRAINT source_id IF NOT EXISTS FOR (n:Source) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT message_id IF NOT EXISTS FOR (n:Message) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT message_hash IF NOT EXISTS FOR (n:Message) REQUIRE n.hash IS UNIQUE;
CREATE CONSTRAINT narrative_id IF NOT EXISTS FOR (n:Narrative) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT event_id IF NOT EXISTS FOR (n:Event) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT wave_id IF NOT EXISTS FOR (n:Wave) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT region_id IF NOT EXISTS FOR (n:Region) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT case_id IF NOT EXISTS FOR (n:AnticorruptionCase) REQUIRE n.id IS UNIQUE;

// ---------- INDEXES ----------
CREATE INDEX message_ts IF NOT EXISTS FOR (n:Message) ON (n.ts_published);
CREATE INDEX event_ts IF NOT EXISTS FOR (n:Event) ON (n.ts);
CREATE INDEX event_type IF NOT EXISTS FOR (n:Event) ON (n.type);
CREATE INDEX wave_date IF NOT EXISTS FOR (n:Wave) ON (n.date);
CREATE INDEX wave_massive IF NOT EXISTS FOR (n:Wave) ON (n.is_massive);
CREATE INDEX case_ts IF NOT EXISTS FOR (n:AnticorruptionCase) ON (n.ts_stage);
CREATE INDEX case_stage IF NOT EXISTS FOR (n:AnticorruptionCase) ON (n.stage);
CREATE INDEX source_type IF NOT EXISTS FOR (n:Source) ON (n.type);

// Векторный индекс для кластеризации нарративов (размерность подставить под вашу embedding-модель)
CREATE VECTOR INDEX message_embedding IF NOT EXISTS
FOR (m:Message) ON (m.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}};

// =====================================================
// МОДЕЛЬ ДАННЫХ (описание полей)
// =====================================================
// (:Source {id, name, country, lang, type, platform, reliability, noise_level})
//    type: official | state_media | milblogger | independent | analyst | osint | agency
//
// (:Message {id, ts_published, ts_collected, lang, text, url, hash,
//            threat_level, novelty, target_mentioned, timeframe_mentioned,
//            is_scheduled_context, llm_confidence, event_kind, embedding})
//    event_kind: rhetoric | operational | anticorruption | decision | other
//
// (:Narrative {id, label, first_seen_ts, last_seen_ts})
//
// (:Entity {id, name, type})            type: person | region | weapon | org | city
//
// (:Event {id, ts, type, region, severity, scheduled})
//    type: decision | summit | holiday | strike_minor | alert | other
//
// (:Wave {id, date, ts_start, ts_end, launched_missiles, launched_drones,
//         intercepted_missiles, intercepted_drones, is_massive, precision,
//         source, confidence, revised_at})
//    is_massive определяется ПОРОГОМ, зафиксированным заранее (см. threshold ниже).
//
// (:Region {id, name, level})           level: oblast | hromada | city
//
// (:AnticorruptionCase {id, side, agency, stage, ts_stage, official_level,
//                       sector, amount, amount_currency, proximity,
//                       source_url, status_note})
//    side:   UA | RU | EU | other
//    agency: НАБУ | САП | ВАКС | БЭБ | СБУ | ОГП | СК РФ | ГП РФ | other
//    stage:  suspicion | detention | preventive_measure | indictment | verdict
//    proximity: none | ministry | defense_procurement | presidential_circle
//    ВАЖНО: хранить СТАТУС дела, не вердикт. suspicion != виновен.
//
// ---------- СВЯЗИ ----------
// (Source)-[:PUBLISHED]->(Message)
// (Message)-[:MENTIONS]->(Entity)
// (Message)-[:PART_OF {similarity}]->(Narrative)
// (Message)-[:CITES]->(Message)
// (Message)-[:REPOSTS]->(Message)
// (Event)-[:TRIGGERED_BY]->(Event)
// (Wave)-[:AFFECTED]->(Region)
// (AnticorruptionCase)-[:INVOLVES]->(Entity)
// (AnticorruptionCase)-[:AMPLIFIED_BY {lag_h}]->(Narrative)
// (AnticorruptionCase)-[:COVERED_BY {lag_h}]->(Message)
// (Narrative)-[:PRECEDED {lag_h}]->(Wave)       // заполняется на этапе анализа

// =====================================================
// ПАРАМЕТРЫ (фиксируются ДО анализа)
// =====================================================
// :param massive_missiles => 7;   // чувствительность: 7..10
// :param massive_drones   => 20;
// Массированный удар (задано владельцем проекта): на ОДИН город >= 7-10 ракет ИЛИ >= 20 дронов.
// Считать по городам (wave -> region), не по суммарному запуску за ночь.

// ---------- Проставить is_massive ----------
// MATCH (w:Wave)
// SET w.is_massive = (w.launched_missiles >= $massive_missiles
//                     OR w.launched_drones >= $massive_drones);

// =====================================================
// ПРИМЕРЫ ЗАПРОСОВ
// =====================================================

// 1) Нарративы, быстро разошедшиеся по многим источникам за 3 дня
// MATCH (s:Source)-[:PUBLISHED]->(m:Message)-[:PART_OF]->(n:Narrative)
// WHERE n.first_seen_ts > datetime() - duration('P3D')
// RETURN n.label, count(DISTINCT s) AS sources, min(m.ts_published) AS first_seen
// ORDER BY sources DESC;

// 2) Антикоррупционные дела и их подхват российскими источниками (лаг в часах)
// MATCH (c:AnticorruptionCase)-[a:AMPLIFIED_BY]->(n:Narrative)<-[:PART_OF]-(m:Message)<-[:PUBLISHED]-(s:Source)
// WHERE s.country = 'RU' AND s.type IN ['state_media','milblogger','official']
// RETURN c.id, c.stage, c.proximity, n.label, a.lag_h, count(DISTINCT s) AS ru_sources
// ORDER BY ru_sources DESC;

// 3) Массированные волны, которым за 1-5 дней предшествовал новый нарратив
// MATCH (w:Wave {is_massive: true})
// OPTIONAL MATCH (n:Narrative)
// WHERE n.first_seen_ts < datetime(w.date)
//   AND n.first_seen_ts > datetime(w.date) - duration('P5D')
// RETURN w.date, collect(n.label) AS preceding_narratives
// ORDER BY w.date;

// 4) Надёжность источника: доля сообщений с threat_level>=2, после которых была массированная волна в 5 дней
// (считать вне графа, в pandas, с контролем на baseline и shuffled-датами)
