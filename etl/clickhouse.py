"""Публикует две готовые витрины PostgreSQL в ClickHouse."""

import os

import clickhouse_connect

from etl.db import operation, sql_file

TABLES = ("market_corridor", "ml_price_predictions")


def client():
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "clickhouse"),
        port=int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "logistics"),
        password=os.environ["CLICKHOUSE_PASSWORD"],
        send_receive_timeout=300,
    )


def publish(settings):
    with operation("clickhouse", settings) as (conn, stats), client() as ch:
        for statement in sql_file("clickhouse/schema.sql").split(";"):
            if statement.strip():
                ch.command(statement)
        password = os.environ["BI_PASSWORD"].replace("\\", "\\\\").replace("'", "\\'")
        ch.command(
            f"CREATE USER IF NOT EXISTS bi_reader IDENTIFIED WITH sha256_password BY '{password}'"
        )
        ch.command("GRANT SELECT ON mart.* TO bi_reader")
        for table in TABLES:
            # Читатели используют старую таблицу, пока мы наполняем и проверяем новую.
            staging = f"mart.{table}_staging"
            ch.command(f"DROP TABLE IF EXISTS {staging} SYNC")
            ch.command(f"CREATE TABLE {staging} AS mart.{table}")
            copied = 0
            with conn.cursor(name=f"export_{table}") as cursor:
                cursor.execute(f"SELECT * FROM mart.{table}")
                columns = [column.name for column in cursor.description]
                while rows := cursor.fetchmany(10000):
                    ch.insert(staging, rows, column_names=columns)
                    copied += len(rows)
            actual = ch.query(f"SELECT count() FROM {staging}").first_row[0]
            if actual != copied:
                raise RuntimeError(f"ClickHouse row count mismatch: {table}: {actual} != {copied}")
            ch.command(f"EXCHANGE TABLES mart.{table} AND {staging}")
            ch.command(f"DROP TABLE {staging} SYNC")
            stats[table] = copied
    return stats
