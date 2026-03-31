import uuid
import logging
from typing import Optional
from fastapi import Request, HTTPException, Depends
from sqlalchemy import text
from src.models.database import async_session_factory

logger = logging.getLogger(__name__)

LEVEL_ORDER = {"viewer": 0, "editor": 1, "owner": 2}


class MatterContext:
    """Matter context attached to request state when matter isolation is enforced."""
    def __init__(self, matter_id: uuid.UUID, user_id: uuid.UUID, access_level: str):
        self.matter_id = matter_id
        self.user_id = user_id
        self.access_level = access_level


async def require_matter(request: Request, min_level: str = "viewer") -> MatterContext:
    """
    Dependency that enforces matter isolation.
    Expects X-Matter-ID header or ?matter_id= query param.
    Verifies user has access. Raises 403 if not.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    if isinstance(user_id, str):
        user_id = uuid.UUID(user_id)

    matter_id = request.headers.get("X-Matter-ID") or request.query_params.get("matter_id")
    if not matter_id:
        raise HTTPException(
            status_code=400,
            detail="Matter ID required. Provide X-Matter-ID header or ?matter_id= query parameter.",
        )

    try:
        matter_id = uuid.UUID(matter_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Matter ID format")

    async with async_session_factory() as session:
        result = await session.execute(
            text("""
                SELECT ma.access_level, m.is_active, m.matter_number, m.name
                FROM matter_access ma
                JOIN matters m ON m.id = ma.matter_id
                WHERE ma.matter_id = :mid AND ma.user_id = :uid
            """),
            {"mid": matter_id, "uid": user_id},
        )
        row = result.fetchone()

    if not row:
        logger.warning(f"User {user_id} attempted unauthorized access to matter {matter_id}")
        raise HTTPException(status_code=403, detail="Access denied: no access to this matter")

    access_level, is_active, matter_number, matter_name = row

    if not is_active:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: matter {matter_number} is archived",
        )

    if LEVEL_ORDER.get(access_level, 0) < LEVEL_ORDER.get(min_level, 0):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: {min_level} level required (you have {access_level})",
        )

    ctx = MatterContext(matter_id=matter_id, user_id=user_id, access_level=access_level)
    request.state.matter = ctx
    request.state.matter_id = matter_id
    request.state.matter_number = matter_number
    request.state.matter_name = matter_name

    return ctx


async def optional_matter(request: Request) -> Optional[MatterContext]:
    """
    Dependency that optionally scopes to a matter if provided.
    Unlike require_matter, this does NOT raise if no matter is given.
    But if a matter IS given, access is verified.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return None

    if isinstance(user_id, str):
        user_id = uuid.UUID(user_id)

    matter_id = request.headers.get("X-Matter-ID") or request.query_params.get("matter_id")
    if not matter_id:
        return None

    try:
        matter_id = uuid.UUID(matter_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Matter ID format")

    async with async_session_factory() as session:
        result = await session.execute(
            text("""
                SELECT ma.access_level, m.is_active, m.matter_number, m.name
                FROM matter_access ma
                JOIN matters m ON m.id = ma.matter_id
                WHERE ma.matter_id = :mid AND ma.user_id = :uid
            """),
            {"mid": matter_id, "uid": user_id},
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=403, detail="Access denied: no access to this matter")

    access_level, is_active, matter_number, matter_name = row

    if not is_active:
        raise HTTPException(status_code=403, detail="Matter is archived")

    ctx = MatterContext(matter_id=matter_id, user_id=user_id, access_level=access_level)
    request.state.matter = ctx
    request.state.matter_id = matter_id
    request.state.matter_number = matter_number
    request.state.matter_name = matter_name

    return ctx
