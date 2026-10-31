import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app
from app.config import TestingConfig
from app.extensions import db as _db
from app.seed import reset_db


class TestConfig(TestingConfig):
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SUPERADMIN_API_TOKEN = "test-superadmin-token"


@pytest.fixture()
def app():
    application = create_app(TestConfig)
    with application.app_context():
        reset_db(application)
    yield application
    with application.app_context():
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register_business(client, email="owner@example.com", business_name="Test Retail Ltd"):
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "Str0ngPass!1",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "business_name": business_name,
            "business_country": "GB",
            "business_currency": "GBP",
            "timezone": "Europe/London",
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]


def auth_headers(access_token):
    return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture()
def owner(client):
    data = register_business(client)
    return {
        "client": client,
        "token": data["access_token"],
        "headers": auth_headers(data["access_token"]),
        "business_id": data["business"]["id"],
        "user_id": data["user"]["id"],
    }
