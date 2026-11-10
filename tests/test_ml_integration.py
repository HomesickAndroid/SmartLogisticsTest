"""Проверяет воспроизводимость прогнозов и правильное представление будущих заявок."""

from datetime import date, timedelta

import pytest

from etl.config import Settings
from etl.db import connect
from etl.pipeline import load_core
from ml.train import train_and_score

pytestmark = pytest.mark.integration


def test_batch_inference_reproducibility_and_no_future_actual(add_price, monkeypatch, tmp_path):
    monkeypatch.setenv("ML_ITERATIONS", "20")
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    for i in range(1, 41):
        add_price(
            i,
            loading_date=date(2026, 8, 1) + timedelta(days=i),
            price=10000 + i * 100,
            distance=100 + i,
        )
    for i in range(41, 47):
        add_price(
            i,
            loading_date=date(2026, 10, 5) + timedelta(days=i - 41),
            price=10000 + i * 100,
            distance=100 + i,
        )
    add_price(
        99,
        loading_date=date(2026, 11, 1),
        price=0,
        is_fact=False,
        price_type=6,
        bodies=[56, 57, 15],
    )
    settings = Settings(date(2026, 10, 26))
    load_core(settings)
    first = train_and_score(settings)
    with connect() as conn:
        rows = conn.execute(
            "SELECT price_id,predicted_price_rub,actual_price_rub,abs_error_rub,ape_pct "
            "FROM mart.ml_price_predictions ORDER BY price_id"
        ).fetchall()
        assert len(rows) == 7
        assert rows[-1][0] == 99 and rows[-1][2:] == (None, None, None)
        # Фильтр должен находить заявку под каждым совместимым кузовом,
        # без повторов внутри одной группы и без размножения основной таблицы прогнозов.
        assert conn.execute(
            "SELECT body_group,body_groups FROM mart.ml_predictions_by_body "
            "WHERE price_id=99 ORDER BY body_group"
        ).fetchall() == [("Борт", "Борт | Тент"), ("Тент", "Борт | Тент")]
    second = train_and_score(settings)
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM mart.ml_models").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT price_id,predicted_price_rub,actual_price_rub,abs_error_rub,ape_pct "
                "FROM mart.ml_price_predictions ORDER BY price_id"
            ).fetchall()
            == rows
        )
    assert first["metrics"] == second["metrics"]
    assert first["model_name"] == second["model_name"]
