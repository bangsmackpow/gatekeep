import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text
from src.models.database import async_session_factory

logger = logging.getLogger(__name__)

router = APIRouter()


class MatterCreate(BaseModel):
    matter_number: str
    name: str
    description: Optional[str] = None
    client_name: Optional[str] = None


class MatterResponse(BaseModel):
    id: uuid.UUID
    matter_number: str
    name: str
    description: Optional[str]
    client_name: Optional[str]
    is_active: bool
    created_at: datetime
    access_level: str


class MatterListResponse(BaseModel):
    matters: List[MatterResponse]
    total: int


class MatterAccessGrant(BaseModel):
    user_email: str
    access_level: str = "viewer"


class MatterUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    client_name: Optional[str] = None


@router.get("/matters", response_model=MatterListResponse)
async def list_matters(request: Request, include_archived: bool = Query(False)):
    user_id = _require_auth(request)

    async with async_session_factory() as session:
        result = await session.execute(
            text("""
                SELECT m.id, m.matter_number, m.name, m.description, m.client_name,
                       m.is_active, m.created_at, ma.access_level
                FROM matters m
                JOIN matter_access ma ON ma.matter_id = m.id
                WHERE ma.user_id = :user_id
                  AND (m.is_active = true OR :include_archived = true)
                ORDER BY m.created_at DESC
            """),
            {"user_id": user_id, "include_archived": include_archived},
        )
        rows = result.fetchall()

    return MatterListResponse(
        matters=[
            MatterResponse(
                id=row[0],
                matter_number=row[1],
                name=row[2],
                description=row[3],
                client_name=row[4],
                is_active=row[5],
                created_at=row[6],
                access_level=row[7],
            )
            for row in rows
        ],
        total=len(rows),
    )


@router.post("/matters", response_model=MatterResponse)
async def create_matter(request: Request, data: MatterCreate):
    user_id = _require_auth(request)

    async with async_session_factory() as session:
        existing = await session.execute(
            text("SELECT id FROM matters WHERE matter_number = :num"),
            {"num": data.matter_number},
        )
        if existing.fetchone():
            raise HTTPException(status_code=409, detail=f"Matter number '{data.matter_number}' already exists")

        matter_id = uuid.uuid4()

        await session.execute(
            text("""
                INSERT INTO matters (id, matter_number, name, description, client_name, created_by, created_at)
                VALUES (:id, :num, :name, :desc, :client, :created_by, NOW())
            """),
            {
                "id": matter_id,
                "num": data.matter_number,
                "name": data.name,
                "desc": data.description,
                "client": data.client_name,
                "created_by": user_id,
            },
        )

        await session.execute(
            text("""
                INSERT INTO matter_access (matter_id, user_id, access_level, granted_by, granted_at)
                VALUES (:matter_id, :user_id, 'owner', :granted_by, NOW())
            """),
            {"matter_id": matter_id, "user_id": user_id, "granted_by": user_id},
        )

        await session.commit()

        logger.info(f"User {user_id} created matter {matter_id} ({data.matter_number})")

    return MatterResponse(
        id=matter_id,
        matter_number=data.matter_number,
        name=data.name,
        description=data.description,
        client_name=data.client_name,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        access_level="owner",
    )


@router.get("/matters/{matter_id}", response_model=MatterResponse)
async def get_matter(request: Request, matter_id: uuid.UUID):
    user_id = _require_auth(request)
    _require_matter_access(user_id, matter_id)

    async with async_session_factory() as session:
        result = await session.execute(
            text("""
                SELECT m.id, m.matter_number, m.name, m.description, m.client_name,
                       m.is_active, m.created_at, ma.access_level
                FROM matters m
                JOIN matter_access ma ON ma.matter_id = m.id
                WHERE m.id = :id AND ma.user_id = :user_id
            """),
            {"id": matter_id, "user_id": user_id},
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Matter not found or access denied")

    return MatterResponse(
        id=row[0],
        matter_number=row[1],
        name=row[2],
        description=row[3],
        client_name=row[4],
        is_active=row[5],
        created_at=row[6],
        access_level=row[7],
    )


@router.patch("/matters/{matter_id}", response_model=MatterResponse)
async def update_matter(request: Request, matter_id: uuid.UUID, data: MatterUpdate):
    user_id = _require_auth(request)
    _require_matter_access(user_id, matter_id, min_level="editor")

    async with async_session_factory() as session:
        updates = {}
        if data.name is not None:
            updates["name"] = data.name
        if data.description is not None:
            updates["description"] = data.description
        if data.client_name is not None:
            updates["client_name"] = data.client_name

        if updates:
            set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
            await session.execute(
                text(f"UPDATE matters SET {set_clauses}, updated_at = NOW() WHERE id = :id"),
                {**updates, "id": matter_id},
            )
            await session.commit()

        result = await session.execute(
            text("""
                SELECT m.id, m.matter_number, m.name, m.description, m.client_name,
                       m.is_active, m.created_at, ma.access_level
                FROM matters m
                JOIN matter_access ma ON ma.matter_id = m.id
                WHERE m.id = :id AND ma.user_id = :user_id
            """),
            {"id": matter_id, "user_id": user_id},
        )
        row = result.fetchone()

    return MatterResponse(
        id=row[0],
        matter_number=row[1],
        name=row[2],
        description=row[3],
        client_name=row[4],
        is_active=row[5],
        created_at=row[6],
        access_level=row[7],
    )


@router.post("/matters/{matter_id}/archive")
async def archive_matter(request: Request, matter_id: uuid.UUID):
    user_id = _require_auth(request)
    _require_matter_access(user_id, matter_id, min_level="owner")

    async with async_session_factory() as session:
        await session.execute(
            text("UPDATE matters SET is_active = false, updated_at = NOW() WHERE id = :id"),
            {"id": matter_id},
        )
        await session.commit()

    logger.info(f"User {user_id} archived matter {matter_id}")
    return {"status": "archived", "matter_id": str(matter_id)}


@router.post("/matters/{matter_id}/unarchive")
async def unarchive_matter(request: Request, matter_id: uuid.UUID):
    user_id = _require_auth(request)
    _require_matter_access(user_id, matter_id, min_level="owner")

    async with async_session_factory() as session:
        await session.execute(
            text("UPDATE matters SET is_active = true, updated_at = NOW() WHERE id = :id"),
            {"id": matter_id},
        )
        await session.commit()

    logger.info(f"User {user_id} unarchived matter {matter_id}")
    return {"status": "unarchived", "matter_id": str(matter_id)}


@router.post("/matters/{matter_id}/access")
async def grant_matter_access(request: Request, matter_id: uuid.UUID, data: MatterAccessGrant):
    user_id = _require_auth(request)
    _require_matter_access(user_id, matter_id, min_level="owner")

    if data.access_level not in ("owner", "editor", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid access level")

    async with async_session_factory() as session:
        target = await session.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": data.user_email.lower()},
        )
        target_row = target.fetchone()
        if not target_row:
            raise HTTPException(status_code=404, detail=f"User '{data.user_email}' not found in system")

        target_user_id = target_row[0]

        await session.execute(
            text("""
                INSERT INTO matter_access (matter_id, user_id, access_level, granted_by, granted_at)
                VALUES (:matter_id, :user_id, :level, :granted_by, NOW())
                ON CONFLICT (matter_id, user_id)
                DO UPDATE SET access_level = :level, granted_by = :granted_by, granted_at = NOW()
            """),
            {
                "matter_id": matter_id,
                "user_id": target_user_id,
                "level": data.access_level,
                "granted_by": user_id,
            },
        )
        await session.commit()

    logger.info(f"User {user_id} granted {data.access_level} on matter {matter_id} to {data.user_email}")
    return {"status": "granted", "user_email": data.user_email, "access_level": data.access_level}


@router.delete("/matters/{matter_id}/access/{user_email}")
async def revoke_matter_access(request: Request, matter_id: uuid.UUID, user_email: str):
    user_id = _require_auth(request)
    _require_matter_access(user_id, matter_id, min_level="owner")

    async with async_session_factory() as session:
        target = await session.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": user_email.lower()},
        )
        target_row = target.fetchone()
        if not target_row:
            raise HTTPException(status_code=404, detail="User not found")

        target_user_id = target_row[0]

        result = await session.execute(
            text("SELECT access_level FROM matter_access WHERE matter_id = :mid AND user_id = :uid"),
            {"mid": matter_id, "uid": target_user_id},
        )
        access_row = result.fetchone()
        if not access_row:
            raise HTTPException(status_code=404, detail="User has no access to this matter")

        if access_row[0] == "owner":
            raise HTTPException(status_code=403, detail="Cannot revoke owner access")

        await session.execute(
            text("DELETE FROM matter_access WHERE matter_id = :mid AND user_id = :uid"),
            {"mid": matter_id, "uid": target_user_id},
        )
        await session.commit()

    return {"status": "revoked", "user_email": user_email}


@router.get("/matters/{matter_id}/access")
async def list_matter_access(request: Request, matter_id: uuid.UUID):
    user_id = _require_auth(request)
    _require_matter_access(user_id, matter_id)

    async with async_session_factory() as session:
        result = await session.execute(
            text("""
                SELECT u.id, u.email, u.display_name, ma.access_level, ma.granted_at
                FROM matter_access ma
                JOIN users u ON u.id = ma.user_id
                WHERE ma.matter_id = :id
                ORDER BY ma.access_level, u.display_name
            """),
            {"id": matter_id},
        )
        rows = result.fetchall()

    return {
        "matter_id": str(matter_id),
        "members": [
            {
                "user_id": str(row[0]),
                "email": row[1],
                "display_name": row[2],
                "access_level": row[3],
                "granted_at": row[4],
            }
            for row in rows
        ],
    }


@router.get("/matters/{matter_id}/stats")
async def get_matter_stats(request: Request, matter_id: uuid.UUID):
    user_id = _require_auth(request)
    _require_matter_access(user_id, matter_id)

    async with async_session_factory() as session:
        total = await session.execute(
            text("SELECT COUNT(*) FROM document_metadata WHERE matter_id = :id"),
            {"id": matter_id},
        )
        total_count = total.scalar()

        by_ext = await session.execute(
            text("""
                SELECT file_extension, COUNT(*) as cnt
                FROM document_metadata WHERE matter_id = :id
                GROUP BY file_extension ORDER BY cnt DESC
            """),
            {"id": matter_id},
        )
        by_extension = {row[0]: row[1] for row in by_ext.fetchall()}

        by_ocr = await session.execute(
            text("""
                SELECT ocr_status, COUNT(*) as cnt
                FROM document_metadata WHERE matter_id = :id
                GROUP BY ocr_status
            """),
            {"id": matter_id},
        )
        by_ocr_status = {row[0]: row[1] for row in by_ocr.fetchall()}

        size = await session.execute(
            text("SELECT COALESCE(SUM(file_size_bytes), 0) FROM document_metadata WHERE matter_id = :id"),
            {"id": matter_id},
        )
        total_bytes = size.scalar()

    return {
        "matter_id": str(matter_id),
        "total_documents": total_count,
        "total_size_bytes": total_bytes,
        "by_extension": by_extension,
        "by_ocr_status": by_ocr_status,
    }


def _require_auth(request: Request) -> uuid.UUID:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return uuid.UUID(user_id) if isinstance(user_id, str) else user_id


def _require_matter_access(user_id: uuid.UUID, matter_id: uuid.UUID, min_level: str = "viewer"):
    import asyncio
    from src.models.database import async_session_factory

    level_order = {"viewer": 0, "editor": 1, "owner": 2}
    min_rank = level_order.get(min_level, 0)

    async def check():
        async with async_session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT ma.access_level, m.is_active
                    FROM matter_access ma
                    JOIN matters m ON m.id = ma.matter_id
                    WHERE ma.matter_id = :mid AND ma.user_id = :uid
                """),
                {"mid": matter_id, "uid": user_id},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=403, detail="Access denied: no access to this matter")
            if not row[1]:
                raise HTTPException(status_code=403, detail="Access denied: matter is archived")
            if level_order.get(row[0], 0) < min_rank:
                raise HTTPException(status_code=403, detail=f"Access denied: {min_level} level required")

    asyncio.run(check())
