import os
import uuid
import logging
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from celery import group
from workers.ingestion.worker import celery_app
from src.config import settings
from src.ingestion.pipeline import process_file
from src.ingestion.models import ExtractedDocument
from src.storage.azure_blob import AzureBlobStorage

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.process_document_batch",
    bind=True,
    queue="ingestion",
    max_retries=3,
    default_retry_delay=60,
)
def process_document_batch(self, task_id: str, user_id: str) -> dict:
    storage = AzureBlobStorage(
        connection_string=settings.AZURE_STORAGE_CONNECTION_STRING,
        container_name=settings.AZURE_STORAGE_CONTAINER,
    )

    prefix = f"{task_id}/"
    processed = 0
    errors = []
    total = 0

    try:
        container = storage._get_container()
        blobs = list(container.list_blobs(name_starts_with=prefix))
        total = len(blobs)

        for blob in blobs:
            try:
                content = storage.download_blob(blob.name)
                filename = Path(blob.name).name

                with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name

                try:
                    documents = process_file(tmp_path, filename)

                    for doc in documents:
                        doc_id = _save_document_to_db_and_es(doc, user_id, task_id)
                        logger.info(f"Processed {filename} -> document {doc_id}")
                        processed += 1

                finally:
                    os.unlink(tmp_path)

            except Exception as e:
                error_msg = f"Failed to process {blob.name}: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

    except Exception as e:
        logger.error(f"Batch processing failed: {e}")
        raise self.retry(exc=e, countdown=60 * (self.request.retries + 1))

    return {
        "processed": processed,
        "total": total,
        "errors": errors,
        "status": "completed" if not errors else "partial",
    }


@celery_app.task(name="tasks.process_single_document", bind=True, queue="ingestion")
def process_single_document(self, file_path: str, filename: str, user_id: str) -> dict:
    documents = process_file(file_path, filename)

    results = []
    for doc in documents:
        doc_id = _save_document_to_db_and_es(doc, user_id)
        results.append({"document_id": str(doc_id), "filename": doc.original_filename})

        if doc.attachments:
            for att in doc.attachments:
                att.parent_document_id = doc_id
                att_id = _save_document_to_db_and_es(att, user_id)
                results.append({"document_id": str(att_id), "filename": att.original_filename, "parent": str(doc_id)})

    return {"documents": results}


@celery_app.task(name="tasks.run_ocr", bind=True, queue="ocr")
def run_ocr(self, document_id: str, blob_name: str) -> dict:
    import requests

    storage = AzureBlobStorage(
        connection_string=settings.AZURE_STORAGE_CONNECTION_STRING,
        container_name=settings.AZURE_STORAGE_CONTAINER,
    )

    content = storage.download_blob(blob_name)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        stirling_url = os.environ.get("STIRLING_PDF_URL", "http://stirling-pdf:8080")

        with open(tmp_path, "rb") as f:
            response = requests.post(
                f"{stirling_url}/api/v1/general/ocr-pdf",
                files={"file": (blob_name, f, "application/pdf")},
                data={"language": "eng"},
                timeout=300,
            )
            response.raise_for_status()
            ocr_text = response.text

        from sqlalchemy import text
        from src.models.database import async_session_factory
        import asyncio

        async def update_ocr():
            async with async_session_factory() as session:
                await session.execute(
                    text("""
                        UPDATE document_metadata
                        SET ocr_status = 'completed', ocr_text_length = :len, processed_at = NOW()
                        WHERE id = :doc_id
                    """),
                    {"len": len(ocr_text), "doc_id": uuid.UUID(document_id)},
                )
                await session.commit()

        asyncio.run(update_ocr())

        return {"document_id": document_id, "ocr_text_length": len(ocr_text), "status": "completed"}

    except Exception as e:
        logger.error(f"OCR failed for {document_id}: {e}")
        return {"document_id": document_id, "status": "failed", "error": str(e)}
    finally:
        os.unlink(tmp_path)


@celery_app.task(name="tasks.index_document", bind=True, queue="index")
def index_document(self, document_id: str) -> dict:
    from elasticsearch import Elasticsearch
    from sqlalchemy import text
    from src.models.database import async_session_factory
    import asyncio

    async def fetch_doc():
        async with async_session_factory() as session:
            result = await session.execute(
                text("SELECT * FROM document_metadata WHERE id = :id"),
                {"id": uuid.UUID(document_id)},
            )
            row = result.fetchone()
            return dict(row._mapping) if row else None

    doc_data = asyncio.run(fetch_doc())
    if not doc_data:
        return {"status": "not_found", "document_id": document_id}

    es = Elasticsearch(
        settings.ELASTICSEARCH_URL,
        basic_auth=(settings.ELASTICSEARCH_USER, settings.ELASTICSEARCH_PASSWORD),
        verify_certs=False,
    )

    es_doc = {
        "document_id": document_id,
        "original_filename": doc_data.get("original_filename", ""),
        "author": doc_data.get("author"),
        "email_subject": doc_data.get("email_subject"),
        "sender_email": doc_data.get("sender_email"),
        "sender_name": doc_data.get("sender_name"),
        "subject": doc_data.get("subject"),
        "title": doc_data.get("title"),
        "extracted_text": doc_data.get("extracted_text", ""),
        "ocr_text": doc_data.get("ocr_text", ""),
        "file_extension": doc_data.get("file_extension", ""),
        "mime_type": doc_data.get("mime_type"),
        "matter_id": str(doc_data["matter_id"]) if doc_data.get("matter_id") else None,
        "sha256_hash": doc_data.get("sha256_hash"),
        "sent_date": doc_data.get("sent_date").isoformat() if doc_data.get("sent_date") else None,
        "received_date": doc_data.get("received_date").isoformat() if doc_data.get("received_date") else None,
        "created_date": doc_data.get("created_date").isoformat() if doc_data.get("created_date") else None,
        "uploaded_at": doc_data.get("uploaded_at").isoformat() if doc_data.get("uploaded_at") else None,
        "language": doc_data.get("language", "en"),
    }

    es.index(index="documents", id=document_id, document=es_doc)

    return {"status": "indexed", "document_id": document_id}


@celery_app.task(name="tasks.cleanup_stale_tasks")
def cleanup_stale_tasks():
    from sqlalchemy import text
    from src.models.database import async_session_factory
    import asyncio

    async def cleanup():
        async with async_session_factory() as session:
            await session.execute(
                text("""
                    UPDATE processing_queue
                    SET status = 'failed', error_message = 'Task timed out'
                    WHERE status = 'processing'
                    AND started_at < NOW() - INTERVAL '2 hours'
                """)
            )
            await session.commit()

    asyncio.run(cleanup())
    return {"status": "cleanup_complete"}


def _save_document_to_db_and_es(doc: ExtractedDocument, user_id: str, task_id: Optional[str] = None) -> uuid.UUID:
    from sqlalchemy import text
    from src.models.database import async_session_factory
    import asyncio

    doc_id = uuid.uuid4()

    async def save():
        async with async_session_factory() as session:
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
                        :ocr_status, :ocr_len, 'completed', :language,
                        :uploaded_by, NOW()
                    )
                """),
                {
                    "id": doc_id,
                    "matter_id": None,
                    "filename": doc.original_filename,
                    "ext": doc.file_extension,
                    "mime": doc.mime_type,
                    "size": doc.file_size_bytes,
                    "sha": doc.sha256_hash,
                    "blob_url": f"pending",
                    "container": settings.AZURE_STORAGE_CONTAINER,
                    "blob_name": f"{task_id or 'manual'}/{doc.original_filename}",
                    "author": doc.author,
                    "title": doc.title,
                    "subject": doc.subject,
                    "created": doc.created_date,
                    "modified": doc.modified_date,
                    "sender_email": doc.sender_email,
                    "sender_name": doc.sender_name,
                    "recipients": doc.recipient_emails,
                    "cc": doc.cc_emails,
                    "sent": doc.sent_date,
                    "received": doc.received_date,
                    "email_subject": doc.email_subject,
                    "message_id": doc.message_id,
                    "in_reply_to": doc.in_reply_to,
                    "has_attachments": doc.has_attachments,
                    "attachment_count": doc.attachment_count,
                    "parent_id": doc.parent_document_id,
                    "ocr_status": "not_needed" if doc.extracted_text else "pending",
                    "ocr_len": len(doc.ocr_text),
                    "language": doc.language,
                    "uploaded_by": uuid.UUID(user_id),
                },
            )
            await session.commit()

    asyncio.run(save())

    from workers.ingestion.tasks import index_document
    index_document.delay(str(doc_id))

    return doc_id
