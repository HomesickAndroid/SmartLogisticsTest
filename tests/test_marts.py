"""Проверяет рыночные показатели, календарные сравнения и отсутствие повторного учёта."""

from datetime import date

import pytest

from etl.config import Settings
from etl.db import connect
from etl.pipeline import build_marts, load_core

pytestmark = pytest.mark.integration
SNAPSHOT = Settings(date(2026, 10, 26))


def test_body_city_month_dedup_and_corridor(add_price):
    for i, price in enumerate([10000, 20000, 30000, 40000], 1):
        add_price(
            i,
            price=price,
            dates=["2026-10-05", "2026-10-20", "2026-11-05"],
            bodies=[56, 57, 61, 15],
        )
    add_price(5, price_type=6, is_fact=False, price=1e8)
    add_price(6, complex=True, price=1e8)
    add_price(7, outlier_mark=True, price=1e8)
    add_price(8, body_type=None, price_type=1)
    load_core(SNAPSHOT)
    build_marts(SNAPSHOT)
    with connect() as conn:
        rows = conn.execute("""SELECT body_group,trips_count,carriers_count,p25_trip,median_trip,p75_trip,
            median_km,mom_pct,yoy_pct FROM mart.market_corridor ORDER BY body_group""").fetchall()
        assert len(rows) == 3
        for row in [rows[0], rows[2]]:
            assert row[1:] == (4, 4, 17500.0, 25000.0, 32500.0, 250.0, None, None)
        assert rows[1][0:3] == ("Не указан", 1, 0)
        assert (
            conn.execute(
                "SELECT cv_rate FROM mart.market_corridor WHERE body_group='Не указан'"
            ).fetchone()[0]
            is None
        )
        assert conn.execute("SELECT max(month) FROM mart.market_corridor").fetchone()[0] == date(
            2026, 10, 1
        )


def test_calendar_changes_reliability_and_last_observation(add_price):
    add_price(1, loading_date=date(2025, 10, 15), price=10000)
    add_price(2, loading_date=date(2026, 8, 15), price=15000)
    for i in range(3, 33):
        add_price(i, loading_date=date(2026, 10, 20), price=20000)
    load_core(SNAPSHOT)
    build_marts(SNAPSHOT)
    with connect() as conn:
        row = conn.execute(
            "SELECT mom_pct,yoy_pct,reliability_level,last_observation_date FROM mart.market_corridor WHERE month='2026-10-01'"
        ).fetchone()
        assert row == (None, 100.0, "HIGH", date(2026, 10, 20))
    build_marts(Settings(date(2026, 12, 1)))
    with connect() as conn:
        assert (
            conn.execute(
                "SELECT reliability_level FROM mart.market_corridor WHERE month='2026-10-01'"
            ).fetchone()[0]
            == "MEDIUM"
        )


def test_exact_previous_month_and_price_dates_not_loading_date(add_price):
    add_price(1, loading_date=date(2026, 8, 15), price=10000, dates=["2026-08-15", "2026-09-15"])
    add_price(2, loading_date=date(2026, 10, 5), price=15000)
    load_core(SNAPSHOT)
    build_marts(SNAPSHOT)
    with connect() as conn:
        assert (
            conn.execute(
                "SELECT mom_pct FROM mart.market_corridor WHERE month='2026-10-01'"
            ).fetchone()[0]
            == 50
        )
