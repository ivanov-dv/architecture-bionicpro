-- CDC: Debezium -> Kafka -> KafkaEngine -> MaterializedView -> актуальные клиенты CRM.

CREATE TABLE IF NOT EXISTS reports.crm_clients_kafka (
    id UInt32, username String, full_name String, prosthesis_model String, issued_at Int32,
    __deleted String, __ts_ms UInt64
) ENGINE = Kafka SETTINGS kafka_broker_list = 'kafka:9092', kafka_topic_list = 'crm.public.clients',
                         kafka_group_name = 'clickhouse', kafka_format = 'JSONEachRow';

-- Версия строки = время события, удалённые помечаются и скрываются при FINAL.
CREATE TABLE IF NOT EXISTS reports.crm_clients_cdc (
    client_id UInt32, username String, full_name String, prosthesis_model String, issued_at Date,
    version UInt64, deleted UInt8
) ENGINE = ReplacingMergeTree(version, deleted) ORDER BY client_id;

CREATE MATERIALIZED VIEW IF NOT EXISTS reports.crm_clients_mv TO reports.crm_clients_cdc AS
SELECT id AS client_id, username, full_name, prosthesis_model, toDate(issued_at) AS issued_at,
       __ts_ms AS version, __deleted = 'true' AS deleted
FROM reports.crm_clients_kafka;

-- Телеметрия: грузит Airflow по дням.
CREATE TABLE IF NOT EXISTS reports.telemetry_raw (
    client_id UInt32, ts DateTime, battery_pct UInt8, movements UInt32, errors UInt16
) ENGINE = MergeTree PARTITION BY toDate(ts) ORDER BY (client_id, ts);

-- Дневные агрегаты: состояния агрегатов корректно сливаются при любом числе блоков вставки.
CREATE TABLE IF NOT EXISTS reports.telemetry_daily (
    client_id UInt32, day Date,
    movements AggregateFunction(sum, UInt32), avg_battery AggregateFunction(avg, UInt8),
    min_battery AggregateFunction(min, UInt8), errors AggregateFunction(sum, UInt16),
    active_hours AggregateFunction(uniqExact, DateTime), processed_at SimpleAggregateFunction(max, DateTime)
) ENGINE = AggregatingMergeTree PARTITION BY day ORDER BY (client_id, day);

CREATE MATERIALIZED VIEW IF NOT EXISTS reports.telemetry_daily_mv TO reports.telemetry_daily AS
SELECT client_id, toDate(ts) AS day,
       sumState(movements) AS movements, avgState(battery_pct) AS avg_battery, minState(battery_pct) AS min_battery,
       sumState(errors) AS errors, uniqExactState(toStartOfHour(ts)) AS active_hours, now() AS processed_at
FROM reports.telemetry_raw
GROUP BY client_id, day;

-- Витрина: агрегаты + актуальные данные клиента из CDC, поэтому изменения CRM видны сразу.
CREATE VIEW IF NOT EXISTS reports.user_daily_report_cdc AS
SELECT c.username, t.day, c.full_name, c.prosthesis_model, c.version AS client_version,
       sumMerge(t.movements) AS movements, avgMerge(t.avg_battery) AS avg_battery, minMerge(t.min_battery) AS min_battery,
       sumMerge(t.errors) AS errors, uniqExactMerge(t.active_hours) AS active_hours, max(t.processed_at) AS processed_at
FROM reports.telemetry_daily t
JOIN (SELECT * FROM reports.crm_clients_cdc FINAL) c ON c.client_id = t.client_id
GROUP BY c.username, t.day, c.full_name, c.prosthesis_model, c.version;
