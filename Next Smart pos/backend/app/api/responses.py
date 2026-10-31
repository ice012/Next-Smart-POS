from flask import jsonify


def envelope(data=None, meta=None, status=200):
    payload = {"data": data if data is not None else {}}
    if meta:
        payload["meta"] = meta
    resp = jsonify(payload)
    resp.status_code = status
    return resp


def ok(data=None, meta=None):
    return envelope(data, meta, 200)


def created(data=None, meta=None):
    return envelope(data, meta, 201)


def unauthorized(message="Refresh token missing"):
    resp = jsonify({"data": {}, "error": "AuthError", "message": message})
    resp.status_code = 401
    return resp


def no_content():
    return ("", 204)


def paginated(items, page, per_page, total):
    pages = (total + per_page - 1) // per_page if per_page else 0
    return ok(
        items,
        meta={
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": pages,
        },
    )


def set_refresh_cookie(resp, token):
    from flask import current_app

    expires = current_app.config["JWT_REFRESH_TOKEN_EXPIRES"]
    resp.set_cookie(
        "refresh_token",
        token,
        max_age=int(expires.total_seconds()),
        httponly=True,
        secure=bool(current_app.config.get("SESSION_COOKIE_SECURE", False)),
        samesite="Lax",
        path="/",
    )
    return resp


def clear_refresh_cookie(resp):
    resp.delete_cookie("refresh_token", path="/", httponly=True, samesite="Lax")
    return resp
