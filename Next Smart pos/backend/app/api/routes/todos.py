from flask import Blueprint, g, request

from ...schemas import TodoCreateSchema, TodoSearchSchema, TodoUpdateSchema
from ...services import todo_service
from ...utils.security import parse_uuid
from ...validation import validate_body, validate_query
from ..permissions import require_permission
from ..responses import created, no_content, ok

todos_bp = Blueprint("todos", __name__)


def _business_id():
    return g.ctx.business_id


@todos_bp.route("", methods=["GET"])
@require_permission("todo.read")
@validate_query(TodoSearchSchema)
def list_todos():
    items, total, pages = todo_service.list_todos(
        _business_id(), request.validated_query
    )
    return ok(
        {"todos": [todo.to_dict() for todo in items]},
        meta={
            "page": request.validated_query.page,
            "per_page": request.validated_query.per_page,
            "total": total,
            "pages": pages,
        },
    )


@todos_bp.route("", methods=["POST"])
@require_permission("todo.write")
@validate_body(TodoCreateSchema)
def create_todo():
    todo = todo_service.create_todo(
        _business_id(), request.validated_body, g.ctx.user_id
    )
    return created({"todo": todo.to_dict()})


@todos_bp.route("/<todo_id>", methods=["GET"])
@require_permission("todo.read")
def get_todo(todo_id):
    todo = todo_service.get_todo(_business_id(), parse_uuid(todo_id))
    return ok({"todo": todo.to_dict()})


@todos_bp.route("/<todo_id>", methods=["PATCH"])
@require_permission("todo.write")
@validate_body(TodoUpdateSchema)
def update_todo(todo_id):
    todo = todo_service.update_todo(
        _business_id(), parse_uuid(todo_id), request.validated_body, g.ctx.user_id
    )
    return ok({"todo": todo.to_dict()})


@todos_bp.route("/<todo_id>", methods=["DELETE"])
@require_permission("todo.delete")
def delete_todo(todo_id):
    todo_service.delete_todo(_business_id(), parse_uuid(todo_id), g.ctx.user_id)
    return no_content()
