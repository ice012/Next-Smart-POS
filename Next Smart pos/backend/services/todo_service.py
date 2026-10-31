from datetime import datetime, timezone

from sqlalchemy import func, or_, select

from ..exceptions import NotFoundError
from ..extensions import db
from ..models import Todo
from ..models.mixins import touch
from ..utils.db import commit
from ..utils.pagination import paginate


def _now():
    return datetime.now(timezone.utc)


def _get_todo(business_id, todo_id):
    todo = db.session.get(Todo, todo_id)
    if todo is None or todo.business_id != business_id or not todo.is_active:
        raise NotFoundError("Todo not found")
    return todo


def list_todos(business_id, search):
    stmt = select(Todo).where(
        Todo.business_id == business_id,
        Todo.is_active.is_(True),
    )
    if search.q:
        pattern = f"%{search.q.strip()}%"
        stmt = stmt.where(
            or_(
                Todo.title.ilike(pattern),
                Todo.description.ilike(pattern),
            )
        )
    if search.completed is not None:
        stmt = stmt.where(Todo.completed.is_(search.completed))
    if search.overdue is not None:
        overdue = (
            Todo.completed.is_(False)
            & Todo.due_at.is_not(None)
            & (Todo.due_at < _now())
        )
        stmt = stmt.where(overdue if search.overdue else ~overdue)
    stmt = stmt.order_by(
        Todo.completed.asc(),
        Todo.position.asc(),
        Todo.due_at.asc(),
        Todo.created_at.desc(),
    )
    items, total, pages = paginate(stmt, search.page, search.per_page)
    return [row[0] for row in items], total, pages


def get_todo(business_id, todo_id):
    return _get_todo(business_id, todo_id)


def create_todo(business_id, data, actor_id):
    position = data.position
    if position is None:
        position = (
            db.session.scalar(
                select(func.coalesce(func.max(Todo.position), -1)).where(
                    Todo.business_id == business_id,
                    Todo.is_active.is_(True),
                )
            )
            + 1
        )
    todo = Todo(
        business_id=business_id,
        title=data.title,
        description=data.description,
        due_at=data.due_at,
        position=position,
    )
    touch(todo, actor_id)
    db.session.add(todo)
    commit()
    return todo


def update_todo(business_id, todo_id, data, actor_id):
    todo = _get_todo(business_id, todo_id)
    if data.title is not None:
        todo.title = data.title
    if "description" in data.model_fields_set:
        todo.description = data.description
    if "due_at" in data.model_fields_set:
        todo.due_at = data.due_at
    if data.completed is not None and data.completed != todo.completed:
        todo.completed = data.completed
        todo.completed_at = _now() if data.completed else None
    if data.position is not None:
        todo.position = data.position
    touch(todo, actor_id)
    commit()
    return todo


def delete_todo(business_id, todo_id, actor_id):
    todo = _get_todo(business_id, todo_id)
    todo.is_active = False
    touch(todo, actor_id)
    commit()
