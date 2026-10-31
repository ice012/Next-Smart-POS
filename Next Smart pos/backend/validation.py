from functools import wraps

from flask import request
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .exceptions import ValidationError as AppValidationError


def format_validation_errors(exc: ValidationError) -> list[dict]:
    """Flatten a pydantic error into JSON-safe loc/msg/type triples.

    Shared so nested objects validated outside a route decorator (the M-Pesa
    block inside the business settings, for example) report failures in the
    same shape as the rest of the API.
    """
    errors = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        errors.append({"loc": loc, "msg": err.get("msg"), "type": err.get("type")})
    return errors


def validate_body(model_cls: type[BaseModel]):
    """Decorator that validates the JSON body against a pydantic model."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Skip validation for OPTIONS (CORS preflight) requests
            if request.method == "OPTIONS":
                return func(*args, **kwargs)
            data = request.get_json(silent=True) or {}
            try:
                parsed = model_cls.model_validate(data)
            except ValidationError as exc:
                raise AppValidationError(
                    "Validation failed", payload={"errors": format_validation_errors(exc)}
                ) from exc
            request.validated_body = parsed
            return func(*args, **kwargs)

        return wrapper

    return decorator


def validate_query(model_cls: type[BaseModel]):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            data = request.args.to_dict()
            try:
                parsed = model_cls.model_validate(data)
            except ValidationError as exc:
                raise AppValidationError(
                    "Validation failed", payload={"errors": format_validation_errors(exc)}
                ) from exc
            request.validated_query = parsed
            return func(*args, **kwargs)

        return wrapper

    return decorator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
