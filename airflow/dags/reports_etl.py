from datetime import datetime

import clickhouse_connect
from airflow import DAG
from airflow.hooks.base import BaseHook
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook

DDL = [
    """CREATE TABLE IF NOT EXISTS crm_clients (
        client_id UInt32, username String, full_name String, prosthesis_model String, issued_at Date
    ) ENGINE = ReplacingMergeTree ORDER BY client_id""",
    """CREATE TABLE IF NOT EXISTS telemetry_raw (
        client_id UInt32, ts DateTime, battery_pct UInt8, movements UInt32, errors UInt16
    ) ENGINE = MergeTree PARTITION BY toDate(ts) ORDER BY (client_id, ts)""",
    # Витрина: одна строка на пользователя и день, ключ по username для быстрой выборки отчёта.
    """CREATE TABLE IF NOT EXISTS user_daily_report (
        username String, day Date, full_name String, prosthesis_model String,
        movements UInt64, avg_battery Float32, min_battery UInt8, errors UInt32, active_hours UInt16,
        processed_at DateTime
    ) ENGINE = ReplacingMergeTree(processed_at) ORDER BY (username, day)""",
]


def clickhouse():
    c = BaseHook.get_connection("clickhouse")
    return clickhouse_connect.get_client(host=c.host, port=c.port, username=c.login, password=c.password, database=c.schema)


def create_tables():
    ch = clickhouse()
    for ddl in DDL:
        ch.command(ddl)


def load_crm():
    rows = PostgresHook("crm").get_records("SELECT id, username, full_name, prosthesis_model, issued_at FROM clients")
    clickhouse().insert("crm_clients", rows, column_names=["client_id", "username", "full_name", "prosthesis_model", "issued_at"])


def load_telemetry(ds, data_interval_start, data_interval_end):
    rows = PostgresHook("telemetry").get_records(
        "SELECT client_id, ts, battery_pct, movements, errors FROM events WHERE ts >= %s AND ts < %s",
        parameters=(data_interval_start, data_interval_end),
    )
    ch = clickhouse()
    ch.command("ALTER TABLE telemetry_raw DROP PARTITION ID %(p)s", {"p": ds.replace("-", "")})  # повторный запуск за день
    if rows:
        ch.insert("telemetry_raw", rows, column_names=["client_id", "ts", "battery_pct", "movements", "errors"])


def build_mart(ds):
    clickhouse().command(
        """INSERT INTO user_daily_report
           SELECT c.username, toDate(t.ts) AS day, c.full_name, c.prosthesis_model,
                  sum(t.movements), avg(t.battery_pct), min(t.battery_pct), sum(t.errors),
                  uniqExact(toStartOfHour(t.ts)), now()
           FROM telemetry_raw t
           JOIN (SELECT * FROM crm_clients FINAL) c ON c.client_id = t.client_id
           WHERE toDate(t.ts) = %(day)s
           GROUP BY c.username, day, c.full_name, c.prosthesis_model""",
        {"day": ds},
    )


with DAG(
    "reports_etl",
    start_date=datetime(2026, 10, 1),
    schedule="@daily",
    catchup=True,          # догоняет пропущенные дни с start_date
    max_active_runs=1,
    default_args={"owner": "airflow", "retries": 1},
) as dag:
    create = PythonOperator(task_id="create_tables", python_callable=create_tables)
    crm = PythonOperator(task_id="load_crm", python_callable=load_crm)
    telemetry = PythonOperator(task_id="load_telemetry", python_callable=load_telemetry)
    mart = PythonOperator(task_id="build_mart", python_callable=build_mart)
    create >> [crm, telemetry] >> mart
