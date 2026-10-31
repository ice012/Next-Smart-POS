from math import ceil

from ..extensions import db


def paginate(query, page: int, per_page: int):
    """Return (items, total, pages) for a SQLAlchemy select query.

    ``.scalars()`` is only safe when the query selects a single ORM entity. A
    multi-entity select such as ``select(BusinessUser, User, Role)`` still
    reports an ``entity`` on its *first* column, so testing the first column
    alone silently collapses each row to that one object and drops the joins.
    Require exactly one selected column as well.
    """
    total = db.session.execute(
        db.select(db.func.count()).select_from(query.subquery())
    ).scalar() or 0
    descriptions = getattr(query, "column_descriptions", None) or []
    single_entity = len(descriptions) == 1 and descriptions[0].get("entity")
    result = db.session.execute(query.offset((page - 1) * per_page).limit(per_page))
    items = result.scalars().all() if single_entity else result.all()
    pages = ceil(total / per_page) if per_page else 0
    return items, total, pages
