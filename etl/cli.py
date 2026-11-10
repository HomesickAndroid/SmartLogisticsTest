"""Общий вход: разбирает команду пользователя и запускает нужные этапы обработки."""

import argparse
import json
import logging
from datetime import date

from etl.config import Settings


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Smart Logistics batch pipeline")
    parser.add_argument(
        "command", choices=["bootstrap", "init", "etl", "mart", "ml", "publish", "run", "status"]
    )
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument(
        "--end-date", type=date.fromisoformat, help="Exclusive loading_date upper bound"
    )
    args = parser.parse_args()
    # Даты из команды имеют приоритет над настройками окружения.
    defaults = Settings.from_env()
    settings = Settings(
        args.as_of or defaults.as_of,
        args.start_date or defaults.start,
        args.end_date or defaults.end,
    )
    from etl.clickhouse import publish
    from etl.initialize import initialize
    from etl.pipeline import build_marts, load_core
    from ml.train import train_and_score

    if args.command in ["init", "bootstrap"]:
        initialize()
    # Порядок важен: сначала очищаем данные, затем считаем рынок, прогнозы и публикацию.
    steps = {"etl": load_core, "mart": build_marts, "ml": train_and_score, "publish": publish}
    if args.command in ["run", "bootstrap"]:
        for function in steps.values():
            function(settings)
    elif args.command in steps:
        steps[args.command](settings)
    elif args.command == "status":
        from etl.db import connect

        with connect() as conn:
            for table in [
                "raw.prices",
                "core.shipments",
                "core.prices",
                "core.dq_quarantine_prices",
                "mart.market_corridor",
                "mart.ml_price_predictions",
            ]:
                print(
                    json.dumps(
                        {
                            "table": table,
                            "rows": conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0],
                        }
                    )
                )
            print(
                json.dumps(
                    conn.execute(
                        "SELECT * FROM mart.ml_quality ORDER BY trained_at DESC LIMIT 1"
                    ).fetchone(),
                    default=str,
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    main()
