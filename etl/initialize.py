"""Создаёт структуру хранилища и один раз заполняет исходные таблицы."""

import hashlib
import os
import subprocess

from psycopg import sql

from etl.config import ROOT
from etl.db import LOCK_ID, connect, event, sql_file


def initialize():
    n_prices = int(os.getenv("N_PRICES", "300000"))
    if n_prices < 1:
        raise ValueError("N_PRICES must be positive")
    # Контрольная сумма выявляет смену исходных SQL: готовые данные не затираются молча.
    fingerprint = hashlib.sha256(
        (sql_file("source/schema.sql") + sql_file("source/synthetic.sql")).encode()
    ).hexdigest()
    with connect(autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            for database in ["airflow", "superset"]:
                if not conn.execute(
                    "SELECT 1 FROM pg_database WHERE datname=%s", (database,)
                ).fetchone():
                    conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            # Собственные таблицы и представления обновляем и при повторном запуске.
            with conn.transaction():
                conn.execute(sql_file("core/schema.sql"))
                conn.execute(sql_file("mart/schema.sql"))
                conn.execute("CREATE SCHEMA IF NOT EXISTS raw")
            version = conn.execute("SELECT fingerprint,n_prices FROM ops.source_version").fetchone()
            if version:
                if version != (fingerprint, n_prices):
                    raise ValueError(
                        "Source fingerprint/N_PRICES changed. Use a separate Compose "
                        "project/volume; existing source will not be overwritten."
                    )
                event("initialize_source", status="already_initialized", n_prices=n_prices)
            else:
                if conn.execute("SELECT to_regclass('raw.prices')").fetchone()[0]:
                    raise ValueError("Unmanaged raw.prices exists without source manifest")
                marker = (
                    sql.SQL("INSERT INTO ops.source_version(fingerprint,n_prices) VALUES ({},{})")
                    .format(sql.Literal(fingerprint), sql.Literal(n_prices))
                    .as_string(conn)
                )
                env = {
                    **os.environ,
                    "PGPASSWORD": os.getenv("PGPASSWORD") or os.environ["POSTGRES_PASSWORD"],
                    "PGOPTIONS": "-c search_path=raw,public",
                }
                # Источник и отметка о его создании сохраняются вместе либо отменяются вместе.
                subprocess.run(
                    [
                        "psql",
                        "-X",
                        "--single-transaction",
                        "-v",
                        "ON_ERROR_STOP=1",
                        "-v",
                        f"n_prices={n_prices}",
                        "-f",
                        str(ROOT / "sql/source/schema.sql"),
                        "-f",
                        str(ROOT / "sql/source/synthetic.sql"),
                        "-c",
                        marker,
                    ],
                    env=env,
                    check=True,
                )
                event("initialize_source", status="created", n_prices=n_prices)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS prices_loading_date_idx ON raw.prices(loading_date)"
            )
            # Для отчётных экранов создаём отдельного читателя без права менять витрины.
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='bi_reader'").fetchone():
                conn.execute(
                    sql.SQL("CREATE ROLE bi_reader LOGIN PASSWORD {}").format(
                        sql.Literal(os.environ["BI_PASSWORD"])
                    )
                )
            conn.execute("GRANT USAGE ON SCHEMA mart TO bi_reader")
            conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA mart TO bi_reader")
            conn.execute(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA mart GRANT SELECT ON TABLES TO bi_reader"
            )
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
