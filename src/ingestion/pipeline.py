import logging
import uuid
from pathlib import Path
from typing import Optional
from src.ingestion.models import ExtractedDocument
from src.ingestion.email_parser import parse_eml, parse_mbox, parse_pst
from src.ingestion.office_parser import parse_office_file
from src.ingestion.pdf_handler import parse_pdf

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    "eml": "email",
    "mbox": "mailbox",
    "pst": "outlook",
    "pdf": "pdf",
    "docx": "office",
    "xlsx": "office",
    "pptx": "office",
    "msg": "email",
    "txt": "text",
    "rtf": "text",
    "csv": "text",
}


def process_file(file_path: str, filename: Optional[str] = None) -> list[ExtractedDocument]:
    if filename is None:
        filename = Path(file_path).name

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in SUPPORTED_EXTENSIONS:
        logger.warning(f"Unsupported file type: .{ext} ({filename})")
        return []

    try:
        if ext == "eml":
            return [parse_eml(file_path, filename)]
        elif ext == "mbox":
            return parse_mbox(file_path)
        elif ext == "pst":
            return parse_pst(file_path)
        elif ext == "pdf":
            doc, needs_ocr = parse_pdf(file_path, filename)
            return [doc]
        elif ext in ("docx", "xlsx", "pptx"):
            return [parse_office_file(file_path, filename)]
        elif ext == "msg":
            return _parse_msg(file_path, filename)
        elif ext in ("txt", "rtf", "csv"):
            return [parse_text_file(file_path, filename)]
        else:
            return []
    except Exception as e:
        logger.error(f"Failed to process {filename}: {e}", exc_info=True)
        return []


def parse_text_file(file_path: str, filename: str) -> ExtractedDocument:
    import hashlib

    with open(file_path, "rb") as f:
        content = f.read()

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
        except Exception:
            text = ""

    return ExtractedDocument(
        original_filename=filename,
        file_extension=filename.rsplit(".", 1)[-1].lower(),
        mime_type="text/plain",
        file_size_bytes=len(content),
        sha256_hash=hashlib.sha256(content).hexdigest(),
        content_bytes=b"",
        extracted_text=text,
    )


def _parse_msg(file_path: str, filename: str) -> list[ExtractedDocument]:
    try:
        import extract_msg
        msg = extract_msg.openMsg(file_path)

        doc = ExtractedDocument(
            original_filename=filename,
            file_extension="msg",
            mime_type="application/vnd.ms-outlook",
            file_size_bytes=Path(file_path).stat().st_size,
            sha256_hash=_file_sha256(file_path),
            content_bytes=b"",
            author=msg.sender,
            sender_name=msg.sender,
            email_subject=msg.subject,
            sent_date=msg.date,
            extracted_text=msg.body or "",
        )

        attachments = []
        for att in msg.attachments:
            if hasattr(att, "data"):
                att_doc = ExtractedDocument(
                    original_filename=att.longFilename or att.shortFilename or "attachment",
                    file_extension=Path(att.longFilename or att.shortFilename or "").suffix.lstrip("."),
                    mime_type=att.mimeType or "application/octet-stream",
                    file_size_bytes=len(att.data),
                    sha256_hash=_bytes_sha256(att.data),
                    content_bytes=att.data,
                    parent_document_id=None,
                )
                attachments.append(att_doc)

        doc.has_attachments = len(attachments) > 0
        doc.attachment_count = len(attachments)
        doc.attachments = attachments

        msg.close()
        return [doc]

    except ImportError:
        logger.error("extract-msg not installed. Run: pip install extract-msg")
        return []
    except Exception as e:
        logger.error(f"Failed to parse MSG file {filename}: {e}")
        return []


def _file_sha256(file_path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _bytes_sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()
