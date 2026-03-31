import logging
import hashlib
from pathlib import Path
from typing import Optional
from src.ingestion.models import ExtractedDocument

logger = logging.getLogger(__name__)


def parse_pdf(file_path: str | bytes, filename: str = "document.pdf") -> ExtractedDocument:
    from pypdf import PdfReader
    import io

    if isinstance(file_path, bytes):
        reader = PdfReader(io.BytesIO(file_path))
        content = file_path
    else:
        reader = PdfReader(file_path)
        with open(file_path, "rb") as f:
            content = f.read()

    text_parts = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text_parts.append(page_text)

    extracted_text = "\n".join(text_parts)
    needs_ocr = len(extracted_text.strip()) < 50

    metadata = reader.metadata or {}

    return ExtractedDocument(
        original_filename=filename,
        file_extension="pdf",
        mime_type="application/pdf",
        file_size_bytes=len(content),
        sha256_hash=hashlib.sha256(content).hexdigest(),
        content_bytes=b"",
        author=metadata.get("/Author"),
        title=metadata.get("/Title"),
        subject=metadata.get("/Subject"),
        created_date=_parse_pdf_date(metadata.get("/CreationDate")),
        modified_date=_parse_pdf_date(metadata.get("/ModDate")),
        extracted_text=extracted_text,
    ), needs_ocr


def parse_pdf_with_ocr(file_path: str, filename: str = "document.pdf", ocr_url: str = "http://stirling-pdf:8080") -> ExtractedDocument:
    import requests

    with open(file_path, "rb") as f:
        pdf_content = f.read()

    try:
        response = requests.post(
            f"{ocr_url}/api/v1/general/ocr-pdf",
            files={"file": (filename, pdf_content, "application/pdf")},
            data={"language": "eng"},
            timeout=120,
        )
        response.raise_for_status()
        ocr_text = response.text
    except Exception as e:
        logger.error(f"Stirling-PDF OCR failed for {filename}: {e}")
        ocr_text = ""

    doc, _ = parse_pdf(file_path, filename)
    doc.ocr_text = ocr_text
    doc.ocr_status = "completed" if ocr_text else "failed"

    return doc


def _parse_pdf_date(pdf_date: Optional[str]):
    if not pdf_date:
        return None
    try:
        pdf_date = pdf_date.strip("D:")
        year = int(pdf_date[:4])
        month = int(pdf_date[4:6]) if len(pdf_date) >= 6 else 1
        day = int(pdf_date[6:8]) if len(pdf_date) >= 8 else 1
        from datetime import datetime, timezone
        return datetime(year, month, day, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None
