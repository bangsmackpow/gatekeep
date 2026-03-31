import uuid
import logging
import aiofiles
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.database import get_db
from src.models.document import DocumentMetadata
from src.storage.azure_blob import AzureBlobStorage
from src.config import settings
from src.middleware.matter_scope import require_matter, MatterContext

logger = logging.getLogger(__name__)

router = APIRouter()


class UploadResponse(BaseModel):
    task_id: str
    matter_id: str
    matter_number: str
    files_queued: int
    message: str


class UploadStatus(BaseModel):
    task_id: str
    status: str
    files_processed: int
    files_total: int
    errors: list[str]


@router.post("/upload", response_model=UploadResponse)
async def upload_files(
    request: Request,
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    matter: MatterContext = Depends(require_matter),
):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    if matter.access_level not in ("owner", "editor"):
        raise HTTPException(status_code=403, detail="Upload requires editor or owner access")

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    storage = AzureBlobStorage(
        connection_string=settings.AZURE_STORAGE_CONNECTION_STRING,
        container_name=settings.AZURE_STORAGE_CONTAINER,
    )

    task_id = str(uuid.uuid4())
    uploaded_count = 0

    for upload_file in files:
        try:
            content = await upload_file.read()
            blob_name = f"{matter.matter_id}/{task_id}/{upload_file.filename}"

            await storage.upload_blob(
                blob_name=blob_name,
                data=content,
                content_type=upload_file.content_type or "application/octet-stream",
            )

            doc = DocumentMetadata(
                id=uuid.uuid4(),
                matter_id=matter.matter_id,
                original_filename=upload_file.filename,
                file_extension=upload_file.filename.rsplit(".", 1)[-1].lower() if "." in upload_file.filename else "",
                mime_type=upload_file.content_type,
                file_size_bytes=len(content),
                sha256_hash=_compute_sha256(content),
                azure_blob_url=f"https://{storage.account_name}.blob.core.windows.net/{settings.AZURE_STORAGE_CONTAINER}/{blob_name}",
                azure_blob_container=settings.AZURE_STORAGE_CONTAINER,
                azure_blob_name=blob_name,
                uploaded_by=uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
            )
            db.add(doc)
            uploaded_count += 1

        except Exception as e:
            logger.error(f"Failed to upload {upload_file.filename}: {e}")

    await db.commit()

    from workers.ingestion.tasks import process_document_batch
    celery_task = process_document_batch.delay(task_id, str(user_id))

    return UploadResponse(
        task_id=celery_task.id,
        matter_id=str(matter.matter_id),
        matter_number=getattr(request.state, "matter_number", ""),
        files_queued=uploaded_count,
        message=f"{uploaded_count} file(s) queued for processing in matter {getattr(request.state, 'matter_number', '')}",
    )


@router.get("/upload/status/{task_id}", response_model=UploadStatus)
async def get_upload_status(task_id: str):
    from celery.result import AsyncResult
    from workers.ingestion.worker import celery_app

    result = AsyncResult(task_id, app=celery_app)

    return UploadStatus(
        task_id=task_id,
        status=result.status,
        files_processed=result.result.get("processed", 0) if result.ready() else 0,
        files_total=result.result.get("total", 0) if result.ready() else 0,
        errors=result.result.get("errors", []) if result.ready() else [],
    )


def _compute_sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()
