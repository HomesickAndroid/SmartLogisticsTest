"""Проверяет разрешённые признаки модели, календарные циклы и границы выборок."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from etl.config import Settings
from ml.features import CATEGORICAL, FEATURES, NUMERIC, boundaries, make_features, split_masks


def test_time_split_boundary_and_future_target_independence():
    frame = pd.DataFrame(
        {
            "loading_date": [
                "2026-07-25",
                "2026-07-26",
                "2026-10-04",
                "2026-10-05",
                "2026-10-26",
                "2026-10-27",
            ],
            "is_fact": [True] * 5 + [False],
            "price_type": [3] * 5 + [6],
            "complex": False,
            "outlier_mark": [False] * 5 + [True],
            "price_rub": [10000] * 5 + [np.nan],
        }
    )
    train, test, future = split_masks(frame, date(2026, 10, 26))
    assert list(np.flatnonzero(train)) == [1, 2]
    assert list(np.flatnonzero(test)) == [3, 4]
    assert list(np.flatnonzero(future)) == [5]
    assert not (train & test).any()
    assert boundaries(date(2026, 10, 26)) == (date(2026, 7, 26), date(2026, 10, 5))


def test_feature_allowlist_and_cycles():
    frame = pd.DataFrame(
        {
            **{col: ["A", None] for col in CATEGORICAL},
            **{col: [1.0, np.nan] for col in NUMERIC},
            "loading_date": ["2026-12-31", "2027-01-01"],
            "price_rub": [123.0, 456.0],
            "price_group_id": [1, 2],
            "profit": [100.0, 200.0],
            "outlier_mark": [False, True],
        }
    )
    actual = make_features(frame)
    assert list(actual) == FEATURES
    for cycle in ["month", "weekday", "day_of_year"]:
        assert np.allclose(actual[f"{cycle}_sin"] ** 2 + actual[f"{cycle}_cos"] ** 2, 1)
    assert abs(actual.day_of_year_sin.iloc[0] - actual.day_of_year_sin.iloc[1]) < 0.03
    changed = frame.assign(
        price_rub=[1e9, -1], price_group_id=[999, 999], profit=0, outlier_mark=True
    )
    pd.testing.assert_frame_equal(actual, make_features(changed))
    assert actual.origin_region.iloc[1] == "Не указан"


def test_invalid_window():
    with pytest.raises(ValueError):
        Settings(date(2026, 10, 26), date(2026, 10, 5), date(2026, 10, 5))
