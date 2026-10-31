from flask import g


class AppError(Exception):
    status_code = 400
    message = "Bad request"

    def __init__(self, message=None, status_code=None, payload=None):
        self.message = message if message is not None else self.message
        self.status_code = status_code if status_code is not None else self.status_code
        self.payload = payload or {}
        super().__init__(self.message)

    def to_dict(self):
        return {"error": self.__class__.__name__, "message": self.message, **self.payload}


class ValidationError(AppError):
    status_code = 422
    message = "Validation failed"


class AuthError(AppError):
    status_code = 401
    message = "Authentication required"


class ForbiddenError(AppError):
    status_code = 403
    message = "Forbidden"


class NotFoundError(AppError):
    status_code = 404
    message = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    message = "Resource conflict"


class BusinessError(AppError):
    status_code = 422
    message = "Business rule violation"


class PlanLimitError(AppError):
    """A subscription plan cap (users/branches/invoices) would be exceeded."""

    status_code = 403
    message = "Subscription plan limit reached"

    def __init__(self, message=None, status_code=None, payload=None):
        super().__init__(message, status_code, payload)


HTTP_STATUS = {
    "CREATED": 201,
    "NO_CONTENT": 204,
    "BAD_REQUEST": 400,
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "UNPROCESSABLE": 422,
    "TOO_MANY": 429,
}
