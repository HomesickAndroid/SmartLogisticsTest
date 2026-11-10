"""Обучает CatBoost, оценивает точность и сохраняет прогнозы для проверки и будущих заявок."""

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from catboost import CatBoostRegressor
from psycopg.types.json import Jsonb
from sklearn.metrics import (
    mean_absolute_error,
    mean_absolute_percentage_error,
    mean_squared_error,
    r2_score,
)

from etl.db import event, operation
from ml.features import (
    CATEGORICAL,
    FEATURE_VERSION,
    FEATURES,
    boundaries,
    make_features,
    split_masks,
)


def metrics(actual, predicted):
    return {
        "r2": float(r2_score(actual, predicted)),
        "mape_pct": float(mean_absolute_percentage_error(actual, predicted) * 100),
        "rmse_rub": float(np.sqrt(mean_squared_error(actual, predicted))),
        "mae_rub": float(mean_absolute_error(actual, predicted)),
    }


def train_and_score(settings):
    with operation("ml", settings) as (conn, stats):
        train_start, test_start = boundaries(settings.as_of)
        cursor = conn.execute(
            "SELECT * FROM core.ml_input WHERE loading_date >= %s ORDER BY loading_date,price_id",
            (train_start,),
        )
        frame = pd.DataFrame(cursor.fetchall(), columns=[c.name for c in cursor.description])
        if frame.empty:
            raise ValueError("No ML input rows; load the source and run ETL first")
        frame["price_rub"] = pd.to_numeric(frame.price_rub, errors="coerce")
        train, test, future = split_masks(frame, settings.as_of)
        if train.sum() < 2 or test.sum() < 2:
            raise ValueError(
                "Need at least two training and two test facts in the configured windows"
            )
        features = make_features(frame)
        parameters = {
            "iterations": int(os.getenv("ML_ITERATIONS", "450")),
            "depth": 7,
            "learning_rate": 0.07,
            "loss_function": "RMSE",
            "random_seed": int(os.getenv("SEED", "20261026")),
            "thread_count": int(os.getenv("ML_THREADS", "4")),
            "has_time": True,
            "allow_writing_files": False,
            "verbose": False,
        }
        # Имя версии зависит от обучающих данных, параметров и версии признаков.
        # Проверочная выборка не участвует в обучении и выборе параметров.
        train_data = features.loc[train].copy()
        train_data["target"] = frame.loc[train, "price_rub"]
        digest = hashlib.sha256(
            pd.util.hash_pandas_object(train_data, index=False).values.tobytes()
        ).hexdigest()
        model_key = hashlib.sha256(
            json.dumps(
                {"data": digest, "parameters": parameters, "features": FEATURE_VERSION},
                sort_keys=True,
            ).encode()
        ).hexdigest()[:16]
        model_name = f"catboost-{settings.as_of}-{model_key}"
        model = CatBoostRegressor(**parameters)
        model.fit(features.loc[train], frame.loc[train, "price_rub"], cat_features=CATEGORICAL)
        predicted_test = np.maximum(model.predict(features.loc[test]), 0.01)
        quality = metrics(frame.loc[test, "price_rub"], predicted_test)
        artifact_dir = Path(os.getenv("ARTIFACT_DIR", "/artifacts")) / model_name
        artifact_dir.mkdir(parents=True, exist_ok=True)
        # Сначала пишем временный файл, затем заменяем готовый: частичный файл не станет моделью.
        model.save_model(str(artifact_dir / "model.tmp.cbm"))
        (artifact_dir / "model.tmp.cbm").replace(artifact_dir / "model.cbm")
        report = {
            "model_name": model_name,
            "as_of": str(settings.as_of),
            "train_start": str(train_start),
            "test_start": str(test_start),
            "train_count": int(train.sum()),
            "test_count": int(test.sum()),
            "future_count": int(future.sum()),
            "metrics": quality,
            "features": FEATURES,
            "parameters": parameters,
            "data_fingerprint": digest,
            "feature_importance": dict(
                zip(FEATURES, model.feature_importances_.tolist(), strict=True)
            ),
        }
        (artifact_dir / "report.tmp.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        (artifact_dir / "report.tmp.json").replace(artifact_dir / "report.json")
        conn.execute(
            """INSERT INTO mart.ml_models
            (model_name,as_of,train_start,test_start,train_count,test_count,metrics,features,
             parameters,data_fingerprint,artifact_path) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(model_name) DO UPDATE SET metrics=EXCLUDED.metrics,
            test_count=EXCLUDED.test_count, artifact_path=EXCLUDED.artifact_path""",
            (
                model_name,
                settings.as_of,
                train_start,
                test_start,
                int(train.sum()),
                int(test.sum()),
                Jsonb(quality),
                Jsonb(FEATURES),
                Jsonb(parameters),
                digest,
                str(artifact_dir / "model.cbm"),
            ),
        )
        # Прогнозы снимка заменяем в одной транзакции, чтобы повтор не создавал дубли.
        conn.execute("DELETE FROM mart.ml_price_predictions WHERE as_of=%s", (settings.as_of,))
        # COPY передаёт много строк за одно соединение вместо отдельного запроса на каждую.
        with psycopg.Cursor(conn) as copy_cursor:
            with copy_cursor.copy("""COPY mart.ml_price_predictions
                (price_id,as_of,loading_date,loading_city_gcid,unloading_city_gcid,origin_city,
                 destination_city,body_group,weight_category,prediction_kind,actual_price_rub,
                 predicted_price_rub,abs_error_rub,ape_pct,model_name) FROM STDIN""") as copy:
                for mask, kind in [(test, "test"), (future, "future")]:
                    subset = frame.loc[mask]
                    if subset.empty:
                        continue
                    prediction = np.maximum(model.predict(features.loc[mask]), 0.01)
                    for row, estimate in zip(subset.itertuples(), prediction, strict=True):
                        # У будущей заявки факт и ошибки всегда пусты, даже если в источнике есть цена.
                        actual = float(row.price_rub) if kind == "test" else None
                        error = abs(actual - float(estimate)) if actual is not None else None
                        copy.write_row(
                            (
                                row.price_id,
                                settings.as_of,
                                row.loading_date,
                                row.loading_city_gcid,
                                row.unloading_city_gcid,
                                row.origin_city,
                                row.destination_city,
                                row.body_group,
                                row.weight_category,
                                kind,
                                actual,
                                float(estimate),
                                error,
                                error / actual * 100 if actual is not None else None,
                                model_name,
                            )
                        )
        stats.update(
            {
                k: report[k]
                for k in ["model_name", "train_count", "test_count", "future_count", "metrics"]
            }
        )
        event("ml_metrics", **stats)
    return stats
