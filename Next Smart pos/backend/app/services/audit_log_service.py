"""Audit logging service.

Records security- and business-relevant events in a tenant-scoped table.
Callers pass the acting user id and a small ``details`` dict; the request
IP / user-agent are captured automatically when called inside a Flask request.
"""
from typing import Optional, Union

from sqlalchemy import select

from ..extensions import db
from ..models import AuditLog
from ..utils.pagination import paginate


def _request_context_info():
    ip = None
    user_agent = None
    try:
        from flask import request

        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr
        user_agent = request.headers.get("User-Agent")
    except RuntimeError:
        pass
    return ip, user_agent


def log_event(
    business_id,
    action: str,
    resource: str,
    actor_id=None,
    resource_id: Optional[Union[str, int]] = None,
    details: Optional[dict] = None,
) -> AuditLog:
    """Append an audit-log row. Never raises; safe to call from any service."""
    try:
        ip, user_agent = _request_context_info()
        entry = AuditLog(
            business_id=business_id,
            user_id=actor_id,
            action=action,
            resource=resource,
            resource_id=str(resource_id) if resource_id is not None else None,
            ip_address=ip,
            user_agent=user_agent,
            details=details,
        )
        db.session.add(entry)
        db.session.flush()
        return entry
    except Exception:
        return None


def list_audit_logs(
    business_id,
    pagination,
    action: Optional[str] = None,
    resource: Optional[str] = None,
    user_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    resource_id: Optional[str] = None,
):
    stmt = select(AuditLog).where(AuditLog.business_id == business_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if resource:
        stmt = stmt.where(AuditLog.resource == resource)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    if from_date:
        stmt = stmt.where(AuditLog.created_at >= from_date)
    if to_date:
        stmt = stmt.where(AuditLog.created_at <= to_date)
    stmt = stmt.order_by(AuditLog.created_at.desc())
    items, total, pages = paginate(stmt, pagination.page, pagination.per_page)
    return items, total, pages


def get_audit_log(business_id, log_id):
    entry = db.session.get(AuditLog, log_id)
    if entry is None or entry.business_id != business_id:
        return None
    return entry


__all__ = ["log_event", "list_audit_logs", "get_audit_log", "AuditLog"]
