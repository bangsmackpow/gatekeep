import uuid
import json
import logging
import zipfile
import tempfile
import io
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from src.models.database import async_session_factory
from src.middleware.matter_scope import require_matter, MatterContext
from src.storage.azure_blob import AzureBlobStorage
from src.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


class MatterExportRequest(BaseModel):
    include_documents: bool = True
    include_metadata: bool = True
    format: str = "zip"


@router.post("/matters/{matter_id}/export")
async def export_matter(
    request: Request,
    matter_id: uuid.UUID,
    data: MatterExportRequest = MatterExportRequest(),
):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    async with async_session_factory() as session:
        access = await session.execute(
            text("""
                SELECT ma.access_level, m.matter_number, m.name, m.client_name, m.description, m.is_active
                FROM matter_access ma
                JOIN matters m ON m.id = ma.matter_id
                WHERE ma.matter_id = :mid AND ma.user_id = :uid
            """),
            {"mid": matter_id, "uid": uuid.UUID(user_id) if isinstance(user_id, str) else user_id},
        )
        row = access.fetchone()
        if not row:
            raise HTTPException(status_code=403, detail="Access denied")

        matter_number, matter_name, client_name, description, is_active = row[1], row[2], row[3], row[4], row[5]

        if not is_active:
            raise HTTPException(status_code=403, detail="Cannot export archived matter")

        docs_result = await session.execute(
            text("""
                SELECT id, original_filename, file_extension, mime_type, file_size_bytes,
                       sha256_hash, azure_blob_name, author, title, subject, created_date,
                       modified_date, sender_email, sender_name, recipient_emails, cc_emails,
                       sent_date, received_date, email_subject, message_id, in_reply_to,
                       has_attachments, attachment_count, parent_document_id, language,
                       uploaded_at
                FROM document_metadata
                WHERE matter_id = :mid
            """),
            {"mid": matter_id},
        )
        documents = docs_result.fetchall()

    manifest = {
        "version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_by": user_id,
        "matter": {
            "id": str(matter_id),
            "matter_number": matter_number,
            "name": matter_name,
            "client_name": client_name,
            "description": description,
        },
        "document_count": len(documents),
        "documents": [],
    }

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))

        if data.include_documents and documents:
            storage = AzureBlobStorage(
                connection_string=settings.AZURE_STORAGE_CONNECTION_STRING,
                container_name=settings.AZURE_STORAGE_CONTAINER,
            )

            docs_dir = "documents/"
            for doc in documents:
                doc_id = str(doc[0])
                filename = doc[1]
                blob_name = doc[6]

                doc_meta = {
                    "document_id": doc_id,
                    "original_filename": filename,
                    "file_extension": doc[2],
                    "mime_type": doc[3],
                    "file_size_bytes": doc[4],
                    "sha256_hash": doc[5],
                    "author": doc[7],
                    "title": doc[8],
                    "subject": doc[9],
                    "created_date": str(doc[10]) if doc[10] else None,
                    "modified_date": str(doc[11]) if doc[11] else None,
                    "sender_email": doc[12],
                    "sender_name": doc[13],
                    "recipient_emails": doc[14],
                    "cc_emails": doc[15],
                    "sent_date": str(doc[16]) if doc[16] else None,
                    "received_date": str(doc[17]) if doc[17] else None,
                    "email_subject": doc[18],
                    "message_id": doc[19],
                    "in_reply_to": doc[20],
                    "has_attachments": doc[21],
                    "attachment_count": doc[22],
                    "parent_document_id": str(doc[23]) if doc[23] else None,
                    "language": doc[24],
                    "uploaded_at": str(doc[25]) if doc[25] else None,
                }

                zf.writestr(f"{docs_dir}{doc_id}.meta.json", json.dumps(doc_meta, indent=2, default=str))

                try:
                    content = await storage.download_blob(blob_name)
                    safe_filename = _safe_filename(filename)
                    zf.writestr(f"{docs_dir}{doc_id}_{safe_filename}", content)
                except Exception as e:
                    logger.error(f"Failed to download blob {blob_name}: {e}")
                    zf.writestr(f"{docs_dir}{doc_id}.error.txt", f"Download failed: {e}")

                manifest["documents"].append({
                    "document_id": doc_id,
                    "filename": filename,
                    "archive_path": f"{docs_dir}{doc_id}_{_safe_filename(filename)}",
                })

        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))

    buffer.seek(0)

    safe_matter_number = matter_number.replace("/", "_").replace("\\", "_")
    filename = f"matter_{safe_matter_number}_{datetime.now().strftime('%Y%m%d')}.zip"

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Matter-Number": matter_number,
            "X-Document-Count": str(len(documents)),
        },
    )


@router.post("/matters/import")
async def import_matter(
    request: Request,
    file: UploadFile = File(...),
):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Import file must be a .zip archive")

    content = await file.read()

    with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
        if "manifest.json" not in zf.namelist():
            raise HTTPException(status_code=400, detail="Invalid import archive: missing manifest.json")

        manifest = json.loads(zf.read("manifest.json"))

        if manifest.get("version") != "1.0":
            raise HTTPException(status_code=400, detail=f"Unsupported manifest version: {manifest.get('version')}")

        matter_info = manifest["matter"]
        original_matter_id = matter_info["id"]

        async with async_session_factory() as session:
            new_matter_id = uuid.uuid4()

            existing = await session.execute(
                text("SELECT id FROM matters WHERE matter_number = :num"),
                {"num": matter_info["matter_number"]},
            )
            if existing.fetchone():
                new_matter_number = f"{matter_info['matter_number']}_imported_{datetime.now().strftime('%Y%m%d%H%M')}"
            else:
                new_matter_number = matter_info["matter_number"]

            await session.execute(
                text("""
                    INSERT INTO matters (id, matter_number, name, description, client_name, created_by, created_at)
                    VALUES (:id, :num, :name, :desc, :client, :created_by, NOW())
                """),
                {
                    "id": new_matter_id,
                    "num": new_matter_number,
                    "name": f"{matter_info['name']} (imported)",
                    "desc": f"Imported from {original_matter_id} on {manifest['exported_at']}",
                    "client": matter_info.get("client_name"),
                    "created_by": uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
                },
            )

            await session.execute(
                text("""
                    INSERT INTO matter_access (matter_id, user_id, access_level, granted_by, granted_at)
                    VALUES (:matter_id, :user_id, 'owner', :granted_by, NOW())
                """),
                {
                    "matter_id": new_matter_id,
                    "user_id": uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
                    "granted_by": uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
                },
            )

            storage = AzureBlobStorage(
                connection_string=settings.AZURE_STORAGE_CONNECTION_STRING,
                container_name=settings.AZURE_STORAGE_CONTAINER,
            )

            imported_count = 0
            id_mapping = {}

            for doc_info in manifest.get("documents", []):
                doc_id = doc_info["document_id"]
                archive_path = doc_info["archive_path"]

                meta_path = archive_path.rsplit("/", 1)[0] + "/" + doc_id + ".meta.json"
                if meta_path not in zf.namelist():
                    continue

                meta = json.loads(zf.read(meta_path))

                if archive_path in zf.namelist():
                    file_content = zf.read(archive_path)
                    blob_name = f"{new_matter_id}/imported/{doc_id}/{meta['original_filename']}"
                    await storage.upload_blob(
                        blob_name=blob_name,
                        data=file_content,
                        content_type=meta.get("mime_type", "application/octet-stream"),
                    )
                    blob_url = f"https://{storage.account_name}.blob.core.windows.net/{settings.AZURE_STORAGE_CONTAINER}/{blob_name}"
                else:
                    blob_name = ""
                    blob_url = ""
                    file_content = b""

                new_doc_id = uuid.uuid4()
                id_mapping[doc_id] = str(new_doc_id)

                await session.execute(
                    text("""
                        INSERT INTO document_metadata (
                            id, matter_id, original_filename, file_extension, mime_type,
                            file_size_bytes, sha256_hash, azure_blob_url, azure_blob_container,
                            azure_blob_name, author, title, subject, created_date, modified_date,
                            sender_email, sender_name, recipient_emails, cc_emails,
                            sent_date, received_date, email_subject, message_id, in_reply_to,
                            has_attachments, attachment_count, parent_document_id,
                            ocr_status, ocr_text_length, extraction_status, language,
                            uploaded_by, uploaded_at
                        ) VALUES (
                            :id, :matter_id, :filename, :ext, :mime,
                            :size, :sha, :blob_url, :container,
                            :blob_name, :author, :title, :subject, :created, :modified,
                            :sender_email, :sender_name, :recipients, :cc,
                            :sent, :received, :email_subject, :message_id, :in_reply_to,
                            :has_attachments, :attachment_count, :parent_id,
                            'pending', 0, 'completed', :language,
                            :uploaded_by, NOW()
                        )
                    """),
                    {
                        "id": new_doc_id,
                        "matter_id": new_matter_id,
                        "filename": meta["original_filename"],
                        "ext": meta["file_extension"],
                        "mime": meta["mime_type"],
                        "size": meta["file_size_bytes"],
                        "sha": meta["sha256_hash"],
                        "blob_url": blob_url,
                        "container": settings.AZURE_STORAGE_CONTAINER,
                        "blob_name": blob_name,
                        "author": meta.get("author"),
                        "title": meta.get("title"),
                        "subject": meta.get("subject"),
                        "created": meta.get("created_date"),
                        "modified": meta.get("modified_date"),
                        "sender_email": meta.get("sender_email"),
                        "sender_name": meta.get("sender_name"),
                        "recipients": meta.get("recipient_emails"),
                        "cc": meta.get("cc_emails"),
                        "sent": meta.get("sent_date"),
                        "received": meta.get("received_date"),
                        "email_subject": meta.get("email_subject"),
                        "message_id": meta.get("message_id"),
                        "in_reply_to": meta.get("in_reply_to"),
                        "has_attachments": meta.get("has_attachments", False),
                        "attachment_count": meta.get("attachment_count", 0),
                        "parent_id": id_mapping.get(meta["parent_document_id"]) if meta.get("parent_document_id") else None,
                        "language": meta.get("language", "en"),
                        "uploaded_by": uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
                    },
                )
                imported_count += 1

            await session.commit()

    logger.info(f"User {user_id} imported matter {new_matter_id} ({new_matter_number}) with {imported_count} documents")

    return {
        "status": "imported",
        "new_matter_id": str(new_matter_id),
        "matter_number": new_matter_number,
        "documents_imported": imported_count,
    }


def _safe_filename(filename: str) -> str:
    import re
    safe = re.sub(r'[^\w\.\-]', '_', filename)
    return safe[:200]
