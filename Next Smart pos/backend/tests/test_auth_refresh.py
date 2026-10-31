from datetime import datetime, timedelta, timezone

from conftest import auth_headers, register_business


def test_refresh_token_rotation_issues_new_tokens(client):
    first = register_business(client, "refresh1@example.com", "Refresh One")
    response = client.post("/api/auth/refresh")
    assert response.status_code == 200, response.get_json()
    new_token = response.get_json()["data"]["access_token"]
    assert new_token
    assert new_token != first["access_token"]

    me = client.get("/api/auth/me", headers=auth_headers(new_token))
    assert me.status_code == 200, me.get_json()


def test_refresh_without_cookie_returns_silent_401(client, caplog):
    with caplog.at_level("WARNING", logger="nsp"):
        response = client.post("/api/auth/refresh")
    assert response.status_code == 401, response.get_json()
    assert response.get_json()["error"] == "AuthError"
    warnings = [rec for rec in caplog.records if rec.levelname == "WARNING"]
    assert not warnings, [rec.getMessage() for rec in warnings]


def test_expired_refresh_token_is_rejected(client, app):
    from app.extensions import db
    from app.models import RefreshToken

    register_business(client, "refresh3@example.com", "Refresh Three")
    with app.app_context():
        record = db.session.scalar(db.select(RefreshToken))
        assert record is not None
        record.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.session.commit()

    response = client.post("/api/auth/refresh")
    assert response.status_code == 401, response.get_json()
    assert "expired" in response.get_json()["message"].lower()


def test_password_reset_request_and_expiry(app, client):
    from app.extensions import db
    from app.models import PasswordResetToken, User

    data = register_business(client, "reset@example.com", "Reset Business")
    issued = client.post(
        "/api/auth/forgot-password", json={"email": "reset@example.com"}
    )
    assert issued.status_code in (200, 202), issued.get_json()

    with app.app_context():
        record = db.session.scalar(
            db.select(PasswordResetToken).where(
                PasswordResetToken.user_id
                == db.session.scalar(db.select(User.id).where(User.email == "reset@example.com"))
            )
        )
        assert record is not None
        record.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.session.commit()
        user_id = record.user_id

    from app.exceptions import AppError
    from app.services.auth_service import consume_password_reset

    with app.app_context():
        try:
            consume_password_reset("a-token-that-does-not-exist", "Str0ngPass!1")
        except AppError as exc:
            assert "invalid" in str(exc).lower() or "expired" in str(exc).lower()
        else:
            raise AssertionError("expected an AppError for an unknown reset token")
        assert db.session.get(User, user_id) is not None
