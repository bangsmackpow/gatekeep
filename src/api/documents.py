import uuid
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.database import get_db
from src.models.document import DocumentMetadata
from src.middleware.matter_scope import require_matter, MatterContext

logger = logging.getLogger(__name__)

router = APIRouter()


class DocumentResponse(BaseModel):
    id: uuid.UUID
    original_filename: str
    file_extension: str
    mime_type: Optional[str]
    file_size_bytes: int
    sha256_hash: str
    author: Optional[str]
    title: Optional[str]
    subject: Optional[str]
    sender_email: Optional[str]
    sender_name: Optional[str]
    email_subject: Optional[str]
    sent_date: Optional[datetime]
    received_date: Optional[datetime]
    ocr_status: str
    extraction_status: str
    uploaded_at: datetime
    matter_id: Optional[uuid.UUID]


class DocumentListResponse(BaseModel):
    matter_id: str
    matter_number: str
    documents: list[DocumentResponse]
    total: int
    page: int
    page_size: int


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    file_extension: Optional[str] = Query(None),
    author: Optional[str] = Query(None),
    sender_email: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    ocr_status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    matter: MatterContext = Depends(require_matter),
):
    query = select(DocumentMetadata).where(DocumentMetadata.matter_id == matter.matter_id)
    count_query = select(func.count()).select_from(DocumentMetadata).where(DocumentMetadata.matter_id == matter.matter_id)

    filters = []
    if file_extension:
        filters.append(DocumentMetadata.file_extension == file_extension.lower())
    if author:
        filters.append(DocumentMetadata.author.ilike(f"%{author}%"))
    if sender_email:
        filters.append(DocumentMetadata.sender_email.ilike(f"%{sender_email}%"))
    if date_from:
        filters.append(DocumentMetadata.sent_date >= date_from)
    if date_to:
        filters.append(DocumentMetadata.sent_date <= date_to)
    if ocr_status:
        filters.append(DocumentMetadata.ocr_status == ocr_status)

    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))

    query = query.order_by(DocumentMetadata.uploaded_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    docs = result.scalars().all()

    count_result = await db.execute(count_query)
    total = count_result.scalar()

    return DocumentListResponse(
        matter_id=str(matter.matter_id),
        matter_number=getattr(request.state, "matter_number", ""),
        documents=[
            DocumentResponse(
                id=d.id,
                original_filename=d.original_filename,
                file_extension=d.file_extension,
                mime_type=d.mime_type,
                file_size_bytes=d.file_size_bytes,
                sha256_hash=d.sha256_hash,
                author=d.author,
                title=d.title,
                subject=d.subject,
                sender_email=d.sender_email,
                sender_name=d.sender_name,
                email_subject=d.email_subject,
                sent_date=d.sent_date,
                received_date=d.received_date,
                ocr_status=d.ocr_status,
                extraction_status=d.extraction_status,
                uploaded_at=d.uploaded_at,
                matter_id=d.matter_id,
            )
            for d in docs
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/documents/{doc_id}", response_model=DocumentResponse)
async def get_document(
    request,
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    matter: MatterContext = Depends(require_matter),
):
    result = await db.execute(
        select(DocumentMetadata).where(
            DocumentMetadata.id == doc_id,
            DocumentMetadata.matter_id == matter.matter_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found in this matter")
    return DocumentResponse(
        id=doc.id,
        original_filename=doc.original_filename,
        file_extension=doc.file_extension,
        mime_type=doc.mime_type,
        file_size_bytes=doc.file_size_bytes,
        sha256_hash=doc.sha256_hash,
        author=doc.author,
        title=doc.title,
        subject=doc.subject,
        sender_email=doc.sender_email,
        sender_name=doc.sender_name,
        email_subject=doc.email_subject,
        sent_date=doc.sent_date,
        received_date=doc.received_date,
        ocr_status=doc.ocr_status,
        extraction_status=doc.extraction_status,
        uploaded_at=doc.uploaded_at,
        matter_id=doc.matter_id,
    )


@router.get("/documents/stats")
async def get_document_stats(
    db: AsyncSession = Depends(get_db),
    matter: MatterContext = Depends(require_matter),
):
    total_result = await db.execute(
        select(func.count()).where(DocumentMetadata.matter_id == matter.matter_id)
    )
    total = total_result.scalar()

    ext_result = await db.execute(
        select(DocumentMetadata.file_extension, func.count().label("count"))
        .where(DocumentMetadata.matter_id == matter.matter_id)
        .group_by(DocumentMetadata.file_extension)
    )
    by_extension = {row[0]: row[1] for row in ext_result.all()}

    ocr_result = await db.execute(
        select(DocumentMetadata.ocr_status, func.count().label("count"))
        .where(DocumentMetadata.matter_id == matter.matter_id)
        .group_by(DocumentMetadata.ocr_status)
    )
    by_ocr_status = {row[0]: row[1] for row in ocr_result.all()}

    return {
        "matter_id": str(matter.matter_id),
        "matter_number": getattr(matter, "matter_number", ""),
        "total_documents": total,
        "by_extension": by_extension,
        "by_ocr_status": by_ocr_status,
    }
