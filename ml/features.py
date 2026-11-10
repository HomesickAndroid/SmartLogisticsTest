"""Готовит признаки сделки и разделяет прошлые и будущие перевозки по датам."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

# В модель допускаем только перечисленные поля: цена и её производные сюда не входят.
CATEGORICAL = [
    "origin_city",
    "origin_region",
    "destination_city",
    "destination_region",
    "body_group",
    "weight_category",
    "volume_category",
]
NUMERIC = [
    "distance",
    "total_weight",
    "total_volume",
    "origin_latitude",
    "origin_longitude",
    "destination_latitude",
    "destination_longitude",
]
CALENDAR = [
    "year",
    "month",
    "weekday",
    "day_of_year",
    "month_sin",
    "month_cos",
    "weekday_sin",
    "weekday_cos",
    "day_of_year_sin",
    "day_of_year_cos",
]
FEATURES = CATEGORICAL + NUMERIC + CALENDAR
FEATURE_VERSION = "1"


def boundaries(as_of: date) -> tuple[date, date]:
    """Возвращает начало обучения и начало проверки за последние три недели."""
    return (pd.Timestamp(as_of) - pd.DateOffset(months=3)).date(), as_of - timedelta(weeks=3)


def split_masks(frame: pd.DataFrame, as_of: date):
    dates = pd.to_datetime(frame.loading_date)
    start, test_start = boundaries(as_of)
    historical = (
        frame.is_fact
        & frame.price_type.isin([1, 2, 3, 4, 5, 9])
        & ~frame.complex
        & ~frame.outlier_mark
        & frame.price_rub.notna()
        & (frame.price_rub > 0)
    )
    train = historical & (dates >= pd.Timestamp(start)) & (dates < pd.Timestamp(test_start))
    test = historical & (dates >= pd.Timestamp(test_start)) & (dates <= pd.Timestamp(as_of))
    # Для будущей заявки нужны параметры перевозки; цена, валюта и разметка цены не мешают прогнозу.
    future = ~frame.complex & (dates > pd.Timestamp(as_of))
    return train, test, future


def make_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Отбирает разрешённые поля, заполняет категории и добавляет признаки календаря."""
    result = frame[CATEGORICAL + NUMERIC].copy()
    for column in CATEGORICAL:
        result[column] = result[column].fillna("Не указан").astype(str)
    for column in NUMERIC:
        result[column] = pd.to_numeric(result[column], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
    dates = pd.to_datetime(frame.loading_date)
    if dates.isna().any():
        raise ValueError("loading_date must not be missing")
    result["year"] = dates.dt.year
    result["month"] = dates.dt.month
    result["weekday"] = dates.dt.dayofweek
    result["day_of_year"] = dates.dt.dayofyear
    # Синус и косинус показывают близость конца и начала цикла: например, декабря и января.
    for column, period, offset in [("month", 12, 1), ("weekday", 7, 0), ("day_of_year", 365.25, 1)]:
        angle = 2 * np.pi * (result[column] - offset) / period
        result[f"{column}_sin"] = np.sin(angle)
        result[f"{column}_cos"] = np.cos(angle)
    return result[FEATURES]
