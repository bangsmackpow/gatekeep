import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, BigInteger, Boolean, DateTime, ForeignKey, func, Index
from sqlalchemy.dialects.postgresql import UUID, ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.models.database import Base


class DocumentMetadata(Base):
    __tablename__ = "document_metadata"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    matter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("matters.id"))

    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(16), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    azure_blob_url: Mapped[str] = mapped_column(Text, nullable=False)
    azure_blob_container: Mapped[str] = mapped_column(String(255), nullable=False)
    azure_blob_name: Mapped[str] = mapped_column(Text, nullable=False)

    author: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    created_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sender_email: Mapped[str | None] = mapped_column(String(255))
    sender_name: Mapped[str | None] = mapped_column(String(255))
    recipient_emails: Mapped[list[str] | None] = mapped_column(ARRAY(String(255)))
    cc_emails: Mapped[list[str] | None] = mapped_column(ARRAY(String(255)))
    sent_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_subject: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(String(512))
    in_reply_to: Mapped[str | None] = mapped_column(String(512))
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    attachment_count: Mapped[int] = mapped_column(Integer, default=0)
    parent_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("document_metadata.id"))

    ocr_status: Mapped[str] = mapped_column(String(32), default="pending")
    ocr_text_length: Mapped[int] = mapped_column(Integer, default=0)
    extraction_status: Mapped[str] = mapped_column(String(32), default="pending")
    language: Mapped[str] = mapped_column(String(16), default="en")

    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_doc_matter", "matter_id"),
        Index("idx_doc_sha256", "sha256_hash"),
        Index("idx_doc_uploaded_by", "uploaded_by"),
        Index("idx_doc_sent_date", "sent_date"),
        Index("idx_doc_extension", "file_extension"),
        Index("idx_doc_sender_email", "sender_email"),
        Index("idx_doc_author", "author"),
        Index("idx_doc_extraction_status", "extraction_status"),
        Index("idx_doc_parent", "parent_document_id"),
    )
