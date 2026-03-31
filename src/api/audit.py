import uuid
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.database import get_db
from src.models.audit_log import AuditLog
from src.middleware.audit import write_audit_log

logger = logging.getLogger(__name__)

router = APIRouter()


class AuditLogResponse(BaseModel):
    id: int
    event_id: uuid.UUID
    action: str
    resource_type: str
    resource_id: Optional[uuid.UUID]
    user_email: Optional[str]
    ip_address: Optional[str]
    timestamp: datetime
    details: Optional[dict]


class AuditLogListResponse(BaseModel):
    logs: list[AuditLogResponse]
    total: int
    page: int
    page_size: int


@router.get("/audit/logs", response_model=AuditLogListResponse)
async def get_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    action: Optional[str] = Query(None),
    user_email: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(AuditLog)
    count_query = select(func.count()).select_from(AuditLog)

    filters = []
    if action:
        filters.append(AuditLog.action == action)
    if user_email:
        filters.append(AuditLog.user_email.ilike(f"%{user_email}%"))
    if resource_type:
        filters.append(AuditLog.resource_type == resource_type)
    if date_from:
        filters.append(AuditLog.timestamp >= date_from)
    if date_to:
        filters.append(AuditLog.timestamp <= date_to)

    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))

    query = query.order_by(AuditLog.timestamp.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    logs = result.scalars().all()

    count_result = await db.execute(count_query)
    total = count_result.scalar()

    return AuditLogListResponse(
        logs=[
            AuditLogResponse(
                id=log.id,
                event_id=log.event_id,
                action=log.action,
                resource_type=log.resource_type,
                resource_id=log.resource_id,
                user_email=log.user_email,
                ip_address=str(log.ip_address) if log.ip_address else None,
                timestamp=log.timestamp,
                details=log.details,
            )
            for log in logs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/audit/logs/{log_id}", response_model=AuditLogResponse)
async def get_audit_log(
    log_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AuditLog).where(AuditLog.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Audit log entry not found")

    return AuditLogResponse(
        id=log.id,
        event_id=log.event_id,
        action=log.action,
        resource_type=log.resource_type,
        resource_id=log.resource_id,
        user_email=log.user_email,
        ip_address=str(log.ip_address) if log.ip_address else None,
        timestamp=log.timestamp,
        details=log.details,
    )


@router.get("/audit/verify-chain")
async def verify_audit_chain(
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AuditLog).order_by(AuditLog.id))
    logs = result.scalars().all()

    if not logs:
        return {"valid": True, "message": "No audit logs to verify"}

    expected_prev_hash = "0" * 64
    broken_at = None

    for log in logs:
        if log.prev_hash != expected_prev_hash:
            broken_at = log.id
            break
        expected_prev_hash = log.row_hash

    if broken_at:
        return {
            "valid": False,
            "message": f"Hash chain broken at log entry {broken_at}",
            "checked_entries": broken_at,
        }

    return {
        "valid": True,
        "message": "Audit log chain integrity verified",
        "total_entries": len(logs),
    }
