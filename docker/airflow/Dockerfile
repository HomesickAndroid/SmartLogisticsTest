FROM apache/airflow:2.11.0-python3.11@sha256:6126ada380e94b4c0442fe72fee20d1d59e1ad10ff14398c4b75a606646ad26e
USER root
# Зависимости обработки и модели изолированы от собственных библиотек Airflow.
RUN python -m venv /opt/pipeline-venv \
    && mkdir -p /artifacts && chown airflow:root /artifacts
COPY requirements.txt /tmp/pipeline-requirements.txt
COPY requirements.lock /tmp/pipeline-constraints.txt
# Зафиксированные версии дают одинаковое окружение при повторной сборке.
RUN /opt/pipeline-venv/bin/pip install --no-cache-dir -r /tmp/pipeline-requirements.txt -c /tmp/pipeline-constraints.txt
COPY --chown=airflow:root . /opt/project
WORKDIR /opt/project
ENV PYTHONPATH=/opt/project PYTHONUNBUFFERED=1
USER airflow
