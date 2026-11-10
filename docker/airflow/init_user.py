"""Создаёт администратора Airflow, если такой пользователь ещё отсутствует."""

import os

from airflow.www.app import create_app

app = create_app()
with app.app_context():
    sm = app.appbuilder.sm
    username = os.getenv("ADMIN_USERNAME", "admin")
    if not sm.find_user(username=username):
        sm.add_user(
            username=username,
            first_name="Local",
            last_name="Admin",
            email="admin@localhost.local",
            role=sm.find_role("Admin"),
            password=os.environ["AIRFLOW_ADMIN_PASSWORD"],
        )
