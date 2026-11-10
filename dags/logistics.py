"""Ежедневно обновляет данные снимка; при ручном запуске можно задать период погрузки."""

import os
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.models.param import Param
from airflow.operators.bash import BashOperator

# Запускаем в 02:30 UTC. Пропущенные дни автоматически не догоняем, параллельные запуски запрещены.
with DAG(
    dag_id="logistics_daily",
    description="DQ → PostgreSQL mart → CatBoost batch prediction → ClickHouse",
    schedule="30 2 * * *",
    start_date=pendulum.datetime(2026, 10, 26, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "logistics", "retries": 2, "retry_delay": timedelta(minutes=1)},
    params={
        "as_of": Param(os.getenv("AS_OF", "2026-10-26"), type="string", format="date"),
        "start_date": Param(None, type=["null", "string"], format="date"),
        "end_date": Param(None, type=["null", "string"], format="date"),
    },
    tags=["logistics", "data-quality", "ml"],
    doc_md="""Daily full reconciliation of the source snapshot. To backfill, trigger with
    `start_date` (inclusive) and `end_date` (exclusive). Dates are passed as environment
    variables, never interpolated into shell commands. `as_of` stays at the snapshot date
    until explicitly changed. The market mart and model are rebuilt from the complete core.""",
) as dag:
    previous = None
    # Зависимости гарантируют, что следующий этап начнётся только после успеха предыдущего.
    for stage in ["etl", "mart", "ml", "publish"]:
        task = BashOperator(
            task_id=stage,
            bash_command=f"/opt/pipeline-venv/bin/python -m etl.cli {stage}",
            cwd="/opt/project",
            append_env=True,
            # Даты передаём через окружение, без вставки пользовательского текста в команду оболочки.
            env={
                "AS_OF": "{{ params.as_of }}",
                "START_DATE": '{{ params.start_date or "" }}',
                "END_DATE": '{{ params.end_date or "" }}',
            },
            execution_timeout=timedelta(minutes=30),
        )
        if previous:
            previous >> task
        previous = task
