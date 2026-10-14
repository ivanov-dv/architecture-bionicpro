from datetime import datetime

import clickhouse_connect
from airflow import DAG
from airflow.hooks.base import BaseHook
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook

# Клиенты CRM приходят через CDC (Debezium -> Kafka), витрина строится MaterializedView — см. clickhouse/init.sql.
# Airflow грузит только телеметрию.


def clickhouse():
    c = BaseHook.get_connection("clickhouse")
    return clickhouse_connect.get_client(host=c.host, port=c.port, username=c.login, password=c.password, database=c.schema)


def load_telemetry(ds, data_interval_start, data_interval_end):
    rows = PostgresHook("telemetry").get_records(
        "SELECT client_id, ts, battery_pct, movements, errors FROM events WHERE ts >= %s AND ts < %s",
        parameters=(data_interval_start, data_interval_end),
    )
    ch = clickhouse()
    for table in ("telemetry_raw", "telemetry_daily"):  # повторный запуск за день: убираем и сырые данные, и агрегаты
        ch.command(f"ALTER TABLE {table} DROP PARTITION ID %(p)s", {"p": ds.replace("-", "")})
    if rows:
        ch.insert("telemetry_raw", rows, column_names=["client_id", "ts", "battery_pct", "movements", "errors"])


with DAG(
    "reports_etl",
    start_date=datetime(2026, 10, 1),
    schedule="@daily",
    catchup=True,          # догоняет пропущенные дни с start_date
    max_active_runs=1,
    default_args={"owner": "airflow", "retries": 1},
) as dag:
    PythonOperator(task_id="load_telemetry", python_callable=load_telemetry)
