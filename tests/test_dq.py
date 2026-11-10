"""Проверяет отсеивание плохих данных, точный курс валют и восстановление категорий."""

from datetime import date
from decimal import Decimal

import pytest

from etl.config import Settings
from etl.db import connect
from etl.pipeline import load_core

pytestmark = pytest.mark.integration
SNAPSHOT = Settings(date(2026, 10, 26))


def test_dq_quarantine_fx_and_inference_candidates(add_price):
    cases = [
        ({"price": 0}, "INVALID_PRICE"),
        ({"price": -1}, "INVALID_PRICE"),
        ({"price": 1000}, "RUB_PRICE_LE_1000"),
        ({"distance": 0}, "INVALID_DISTANCE"),
        ({"total_weight": -1}, "INVALID_WEIGHT"),
        ({"total_volume": -1}, "INVALID_VOLUME"),
        ({"loading_city_gcid": 999}, "UNKNOWN_ORIGIN"),
        ({"unloading_city_gcid": 999}, "UNKNOWN_DESTINATION"),
        ({"currency_code": "840", "nearest_rate_date": None}, "MISSING_EXACT_FX_RATE"),
        ({"currency_code": "840", "nearest_rate_date": "2026-10-03"}, "MISSING_EXACT_FX_RATE"),
        ({"currency_code": "999"}, "UNSUPPORTED_CURRENCY"),
        ({"price": float("nan")}, "INVALID_PRICE"),
        ({"distance": float("inf")}, "INVALID_DISTANCE"),
    ]
    for i, (changes, _) in enumerate(cases, 1):
        add_price(i, **changes)
    add_price(50, price=100, currency_code="840", nearest_rate_date="2026-10-02")
    add_price(51, unloading_city_gcid=1001, price=1000.01)
    add_price(52, outlier_mark=True)
    add_price(53, loading_date=date(2026, 11, 1), price=0)
    load_core(SNAPSHOT)
    with connect() as conn:
        quarantine = dict(
            conn.execute("SELECT price_id,reasons FROM core.dq_quarantine_prices").fetchall()
        )
        for i, (_, expected) in enumerate(cases, 1):
            assert expected in quarantine[i]
        assert conn.execute("SELECT price_rub FROM core.prices WHERE price_id=50").fetchone()[
            0
        ] == Decimal("9000")
        assert (
            conn.execute("SELECT count(*) FROM core.prices WHERE price_id IN (51,52)").fetchone()[0]
            == 2
        )
        assert (
            conn.execute("SELECT price_rub FROM core.ml_input WHERE price_id=53").fetchone()[0]
            is None
        )
        assert (
            conn.execute("SELECT region FROM core.cities WHERE gc_id=1001").fetchone()[0]
            == "г. Москва"
        )


@pytest.mark.parametrize(
    "weight,expected",
    [
        (0, "OTH"),
        (0.44, "OTH"),
        (0.45, "L5T"),
        (2.45, "5T"),
        (4.95, "10T"),
        (9.95, "FTL"),
        (29.95, "G20T"),
        (30, "G20T"),
    ],
)
def test_rounded_categories(add_price, weight, expected):
    add_price(1, total_weight=weight, total_volume=120)
    add_price(2, total_weight=weight, total_volume=0, weight_category="FTL")
    load_core(SNAPSHOT)
    with connect() as conn:
        rows = conn.execute(
            "SELECT weight_category,volume_category FROM core.shipments ORDER BY price_id"
        ).fetchall()
    assert rows == [(expected, "FTL"), ("FTL", "OTH")]
