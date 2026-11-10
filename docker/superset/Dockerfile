FROM apache/superset:5.0.0@sha256:09735adaae3945c5a8fabbeca31a7954c8c74de195702ba61622aee8604d5c55
USER root
# Добавляем драйверы чтения витрин PostgreSQL и ClickHouse в окружение Superset.
RUN uv pip install --python /app/.venv/bin/python psycopg2-binary==2.9.10 clickhouse-connect==0.8.18
COPY docker/superset/config.py /app/pythonpath/superset_config.py
COPY docker/superset/init.py /app/init_dashboard.py
COPY docker/superset/start.sh /app/start.sh
USER superset
