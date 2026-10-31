import re
import uuid as uuidlib

from werkzeug.security import check_password_hash, generate_password_hash

from ..exceptions import ValidationError

_PWD_MIN_DEFAULT = 8
_PWD_MAX_DEFAULT = 128


def hash_password(password: str, method: str = "scrypt") -> str:
    return generate_password_hash(password, method=method)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return check_password_hash(password_hash, password)
    except ValueError:
        return False


def validate_password_strength(
    password: str, min_len: int = _PWD_MIN_DEFAULT, max_len: int = _PWD_MAX_DEFAULT
) -> None:
    if not isinstance(password, str) or len(password) < min_len:
        raise ValidationError(f"Password must be at least {min_len} characters")
    if len(password) > max_len:
        raise ValidationError(f"Password must be at most {max_len} characters")
    if not re.search(r"[A-Z]", password):
        raise ValidationError("Password must contain an uppercase letter")
    if not re.search(r"[a-z]", password):
        raise ValidationError("Password must contain a lowercase letter")
    if not re.search(r"[0-9]", password):
        raise ValidationError("Password must contain a digit")


def is_valid_uuid(value) -> bool:
    try:
        uuidlib.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def to_uuid(value) -> uuidlib.UUID:
    if value is None:
        return None
    return uuidlib.UUID(str(value))


def parse_uuid(value, name: str = "id"):
    if not is_valid_uuid(value):
        raise ValidationError(f"{name} must be a valid UUID")
    return to_uuid(value)
