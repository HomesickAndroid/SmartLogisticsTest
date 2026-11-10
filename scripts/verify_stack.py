"""Сверяет результаты и сервисы; с --replay повторяет обработку и проверяет неизменность данных."""

import argparse
import base64
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from etl.clickhouse import client, publish
from etl.config import Settings
from etl.db import connect
from etl.initialize import initialize
from etl.pipeline import build_marts, load_core
from ml.train import train_and_score


def fingerprints():
    """Считает число строк и контрольную сумму их содержимого, исключая время расчёта прогноза."""
    result = {}
    with connect() as conn:
        for table in [
            "core.shipments",
            "core.prices",
            "core.price_body_groups",
            "core.price_dates",
            "core.dq_quarantine_prices",
            "mart.market_corridor",
            "mart.ml_price_predictions",
        ]:
            result[table] = (
                conn.execute(f"""SELECT count(*), md5(string_agg(value, '' ORDER BY value))
                FROM (SELECT (to_jsonb(t)-'scored_at')::text AS value FROM {table} t) x""").fetchone()
            )
    return result


def request_json(url, data=None, headers=None):
    request = Request(
        url,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def verify_services():
    """Проверяет Airflow и вход в Superset, наличие рабочего расписания и пяти визуализаций."""
    result = {}
    health = request_json("http://airflow-webserver:8080/health")
    assert health["metadatabase"]["status"] == "healthy", health
    assert health["scheduler"]["status"] == "healthy", health
    credentials = f"{os.getenv('ADMIN_USERNAME', 'admin')}:{os.environ['AIRFLOW_ADMIN_PASSWORD']}"
    dag = request_json(
        "http://airflow-webserver:8080/api/v1/dags/logistics_daily",
        headers={"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()},
    )
    assert dag["is_active"] and not dag["is_paused"], dag
    runs = request_json(
        "http://airflow-webserver:8080/api/v1/dags/logistics_daily/dagRuns?limit=10&order_by=-execution_date",
        headers={"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()},
    )
    assert any(run["state"] == "success" for run in runs["dag_runs"]), runs
    result["airflow"] = {
        "dag_id": dag["dag_id"],
        "active": dag["is_active"],
        "successful_runs": sum(run["state"] == "success" for run in runs["dag_runs"]),
    }
    login = request_json(
        "http://superset:8088/api/v1/security/login",
        {
            "username": os.getenv("ADMIN_USERNAME", "admin"),
            "password": os.environ["SUPERSET_ADMIN_PASSWORD"],
            "provider": "db",
            "refresh": True,
        },
    )
    headers = {"Authorization": "Bearer " + login["access_token"]}
    dashboards = request_json("http://superset:8088/api/v1/dashboard/", headers=headers)
    ours = [d for d in dashboards["result"] if d["slug"] == "smart-logistics"]
    assert len(ours) == 1, dashboards
    charts = request_json(
        f"http://superset:8088/api/v1/dashboard/{ours[0]['id']}/charts", headers=headers
    )
    assert len(charts["result"]) == 5, charts
    result["superset"] = {"dashboard": "smart-logistics", "charts": len(charts["result"])}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--services", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    before = fingerprints()
    # Повторный запуск обязан дать те же бизнес-данные, несмотря на новые служебные даты.
    if args.replay:
        initialize()
        load_core(settings)
        build_marts(settings)
        train_and_score(settings)
        publish(settings)
        assert fingerprints() == before, "Business data changed on an identical replay"
    comparisons = {}
    with connect() as conn, client() as ch:
        for table, column in [
            ("market_corridor", "median_trip"),
            ("ml_price_predictions", "predicted_price_rub"),
        ]:
            pg = conn.execute(f"SELECT count(*),sum({column}) FROM mart.{table}").fetchone()
            ck = ch.query(f"SELECT count(),sum({column}) FROM mart.{table}").first_row
            assert pg[0] == ck[0], (table, pg, ck)
            # Небольшой допуск нужен из-за разного порядка сложения дробных чисел в двух СУБД.
            assert abs(pg[1] - ck[1]) <= max(1e-6, abs(pg[1]) * 1e-12), (table, pg, ck)
            comparisons[table] = {"postgres_rows": pg[0], "clickhouse_rows": ck[0], "sum": pg[1]}
        assert (
            conn.execute("""SELECT count(*) FROM mart.ml_price_predictions WHERE prediction_kind='future'
            AND (actual_price_rub IS NOT NULL OR abs_error_rub IS NOT NULL OR ape_pct IS NOT NULL)""").fetchone()[
                0
            ]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM mart.market_corridor WHERE month > %s", (settings.as_of,)
            ).fetchone()[0]
            == 0
        )
        quality = conn.execute(
            "SELECT to_jsonb(t) FROM mart.ml_quality t ORDER BY trained_at DESC LIMIT 1"
        ).fetchone()[0]
        raw_count = conn.execute("SELECT count(*) FROM raw.prices").fetchone()[0]
    report = {
        "source_rows": raw_count,
        "full_replay_identical": args.replay,
        "fingerprints": before,
        "clickhouse_parity": comparisons,
        "ml": quality,
    }
    if args.services:
        report["services"] = verify_services()
    path = Path(os.getenv("ARTIFACT_DIR", "/artifacts")) / "validation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
