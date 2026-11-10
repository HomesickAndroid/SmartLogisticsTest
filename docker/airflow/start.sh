#!/usr/bin/env bash
set -euo pipefail
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="$(/opt/pipeline-venv/bin/python -m etl.config airflow-url)"
export AIRFLOW__CORE__FERNET_KEY="$AIRFLOW_FERNET_KEY"
export AIRFLOW__WEBSERVER__SECRET_KEY="$AIRFLOW_WEB_SECRET"
if [ "${1:-}" = webserver ]; then
  # Подготовка происходит один раз перед веб-сервером; планировщик ждёт его готовности.
  airflow db migrate
  python /opt/project/docker/airflow/init_user.py
fi
exec airflow "$@"
