import uuid
import logging
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExtractedDocument:
    original_filename: str
    file_extension: str
    mime_type: str
    file_size_bytes: int
    sha256_hash: str
    content_bytes: bytes

    author: Optional[str] = None
    title: Optional[str] = None
    subject: Optional[str] = None
    created_date: Optional[datetime] = None
    modified_date: Optional[datetime] = None

    sender_email: Optional[str] = None
    sender_name: Optional[str] = None
    recipient_emails: list = field(default_factory=list)
    cc_emails: list = field(default_factory=list)
    sent_date: Optional[datetime] = None
    received_date: Optional[datetime] = None
    email_subject: Optional[str] = None
    message_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    has_attachments: bool = False
    attachment_count: int = 0

    extracted_text: str = ""
    ocr_text: str = ""
    language: str = "en"

    attachments: list["ExtractedDocument"] = field(default_factory=list)
    parent_document_id: Optional[uuid.UUID] = None
