from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from ..extensions import db
from ..exceptions import ConflictError


def commit():
    """Commit the session, converting unique-constraint violations to 409."""
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ConflictError("A record with these values already exists") from exc


def get_or_404(model, pk, business_id=None):
    obj = db.session.get(model, pk)
    if obj is None:
        from ..exceptions import NotFoundError
        raise NotFoundError(f"{model.__name__} not found")
    return obj
