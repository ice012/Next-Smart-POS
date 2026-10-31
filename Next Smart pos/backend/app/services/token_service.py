import uuid as uuidlib
from datetime import datetime, timezone

import jwt

from ..exceptions import AppError, AuthError
from ..models.iam import RefreshToken, hash_token


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cfg():
    from flask import current_app

    return current_app.config


def create_access_token(user_id, business_id) -> str:
    cfg = _cfg()
    now = _now()
    payload = {
        "jti": str(uuidlib.uuid4()),
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "nbf": now,
        "exp": now + cfg["JWT_ACCESS_TOKEN_EXPIRES"],
    }
    if business_id is not None:
        payload["bid"] = str(business_id)
    return jwt.encode(payload, cfg["JWT_SECRET_KEY"], algorithm=cfg["JWT_ALGORITHM"])


def create_refresh_token(user_id, business_id) -> tuple[str, RefreshToken]:
    cfg = _cfg()
    now = _now()
    jti = str(uuidlib.uuid4())
    payload = {
        "jti": jti,
        "sub": str(user_id),
        "type": "refresh",
        "iat": now,
        "nbf": now,
        "exp": now + cfg["JWT_REFRESH_TOKEN_EXPIRES"],
    }
    if business_id is not None:
        payload["bid"] = str(business_id)
    token = jwt.encode(payload, cfg["JWT_SECRET_KEY"], algorithm=cfg["JWT_ALGORITHM"])
    record = RefreshToken(
        id=uuidlib.uuid4(),
        user_id=user_id,
        business_id=business_id,
        jti=jti,
        token_hash=hash_token(token),
        issued_at=now,
        expires_at=now + cfg["JWT_REFRESH_TOKEN_EXPIRES"],
        revoked=False,
    )
    return token, record


def decode_token(token: str) -> dict:
    cfg = _cfg()
    try:
        return jwt.decode(
            token, cfg["JWT_SECRET_KEY"], algorithms=[cfg["JWT_ALGORITHM"]]
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid token") from exc


def decode_access_token(token: str) -> dict:
    claims = decode_token(token)
    if claims.get("type") != "access":
        raise AuthError("Invalid token type")
    return claims


def decode_refresh_token(token: str) -> dict:
    claims = decode_token(token)
    if claims.get("type") != "refresh":
        raise AuthError("Invalid token type")
    return claims


def create_privileged_token(user_id, business_id, action, ttl) -> str:
    """Mint a short-lived token authorising one action in one business.

    Used for actions a lesser role may request but not perform unaided. It
    carries no authority of its own: `privileged_service` checks the action and
    the business against the caller's own request before honouring it.
    """
    cfg = _cfg()
    now = _now()
    payload = {
        "jti": str(uuidlib.uuid4()),
        "sub": str(user_id),
        "type": "privileged",
        "act": action,
        "bid": str(business_id),
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, cfg["JWT_SECRET_KEY"], algorithm=cfg["JWT_ALGORITHM"])


def decode_privileged_token(token: str) -> dict:
    claims = decode_token(token)
    if claims.get("type") != "privileged":
        raise AuthError("Invalid token type")
    return claims


def find_refresh_token(token: str):
    from ..extensions import db
    from ..models.iam import RefreshToken

    stmt = (
        RefreshToken.__table__.select()
        .where(RefreshToken.token_hash == hash_token(token), RefreshToken.revoked.is_(False))
    )
    return db.session.scalar(stmt)
