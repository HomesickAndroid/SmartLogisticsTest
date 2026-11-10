"""Проверяет повторные загрузки, изменения источника и отмену записи при ошибке."""

from datetime import date

import pytest

from etl.config import Settings
from etl.db import connect, operation, sql_file
from etl.pipeline import build_marts, load_core

pytestmark = pytest.mark.integration
SNAPSHOT = Settings(date(2026, 10, 26))


def fingerprint():
    with connect() as conn:
        return {
            table: conn.execute(
                f"SELECT md5(string_agg(row_to_json(t)::text,'' ORDER BY row_to_json(t)::text)) FROM {table} t"
            ).fetchone()[0]
            for table in [
                "core.shipments",
                "core.prices",
                "core.price_dates",
                "core.price_body_groups",
                "core.dq_quarantine_prices",
                "mart.market_corridor",
            ]
        }


def test_repeat_and_window_reconciliation(add_price):
    add_price(1, price=12000)
    add_price(2, price=0)
    add_price(3, loading_date=date(2026, 9, 1))
    load_core(SNAPSHOT)
    build_marts(SNAPSHOT)
    before = fingerprint()
    load_core(SNAPSHOT)
    build_marts(SNAPSHOT)
    assert fingerprint() == before
    with connect() as conn:
        conn.execute("UPDATE raw.prices SET loading_date='2026-11-01',price=15000 WHERE id=1")
        conn.execute("UPDATE raw.prices SET price=12000 WHERE id=2")
    window = Settings(SNAPSHOT.as_of, date(2026, 10, 1), date(2026, 11, 1))
    load_core(window)
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM core.dq_quarantine_prices").fetchone()[0] == 0
        assert conn.execute("SELECT loading_date FROM core.shipments WHERE price_id=1").fetchone()[
            0
        ] == date(2026, 11, 1)
        assert conn.execute("SELECT count(*) FROM core.prices").fetchone()[0] == 3
        conn.execute("DELETE FROM raw.prices WHERE id=2")
    load_core(window)
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM core.prices WHERE price_id=2").fetchone()[0] == 0


def test_failed_transaction_keeps_previous_snapshot(add_price):
    add_price(1)
    load_core(SNAPSHOT)
    build_marts(SNAPSHOT)
    before = fingerprint()
    with pytest.raises(RuntimeError):
        with operation("forced_failure_test", SNAPSHOT) as (conn, _):
            conn.execute(sql_file("core/load.sql"), {"start": None, "end": None})
            raise RuntimeError("intentional failure before commit")
    assert fingerprint() == before
    with connect() as conn:
        assert (
            conn.execute(
                "SELECT status FROM ops.runs WHERE stage='forced_failure_test'"
            ).fetchone()[0]
            == "failed"
        )
