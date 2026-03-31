import logging
import mailbox
import email
from email.header import decode_header
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from src.ingestion.models import ExtractedDocument

logger = logging.getLogger(__name__)


def _decode_header_value(value) -> Optional[str]:
    if value is None:
        return None
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _parse_date(date_str) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


def _extract_email_addresses(header_value) -> list[str]:
    if not header_value:
        return []
    import re
    addresses = re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', header_value)
    return [a.lower() for a in addresses]


def parse_eml(file_path: str | bytes, filename: str = "message.eml") -> ExtractedDocument:
    if isinstance(file_path, bytes):
        msg = email.message_from_bytes(file_path)
        content = file_path
    else:
        with open(file_path, "rb") as f:
            content = f.read()
        msg = email.message_from_bytes(content)

    body_text = ""
    body_html = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body_text += payload.decode(charset, errors="replace")
            elif content_type == "text/html" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body_html += payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body_text = payload.decode(charset, errors="replace")

    extracted_text = body_text
    if not extracted_text and body_html:
        import re
        extracted_text = re.sub(r"<[^>]+>", "", body_html)

    doc = ExtractedDocument(
        original_filename=filename,
        file_extension="eml",
        mime_type="message/rfc822",
        file_size_bytes=len(content),
        sha256_hash=_compute_sha256(content),
        content_bytes=b"",
        author=_decode_header_value(msg.get("From")),
        email_subject=_decode_header_value(msg.get("Subject")),
        sender_email=_extract_email_addresses(msg.get("From", ""))[0] if msg.get("From") else None,
        sender_name=_decode_header_value(msg.get("From")),
        recipient_emails=_extract_email_addresses(msg.get("To", "")),
        cc_emails=_extract_email_addresses(msg.get("Cc", "")),
        sent_date=_parse_date(msg.get("Date")),
        message_id=msg.get("Message-ID"),
        in_reply_to=msg.get("In-Reply-To"),
        extracted_text=extracted_text.strip(),
    )

    attachment_count = 0
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition") and "attachment" in part.get("Content-Disposition", ""):
                attachment_count += 1

    doc.has_attachments = attachment_count > 0
    doc.attachment_count = attachment_count

    return doc


def parse_mbox(file_path: str) -> list[ExtractedDocument]:
    documents = []
    mbox = mailbox.mbox(file_path)

    for i, message in enumerate(mbox):
        filename = f"mbox_message_{i+1}.eml"
        try:
            doc = parse_eml(message.as_bytes(), filename)
            doc.file_extension = "mbox"
            documents.append(doc)
        except Exception as e:
            logger.error(f"Failed to parse mbox message {i}: {e}")

    return documents


def parse_pst(file_path: str) -> list[ExtractedDocument]:
    try:
        import pypff
    except ImportError:
        logger.error("pypff not installed. Install libpff-python for PST support.")
        return []

    documents = []
    pst = pypff.file()
    pst.open(file_path)

    try:
        root = pst.get_root_folder()
        documents = _process_pst_folder(root, Path(file_path).stem)
    finally:
        pst.close()

    return documents


def _process_pst_folder(folder, parent_name: str, depth: int = 0) -> list[ExtractedDocument]:
    documents = []
    indent = "  " * depth

    for sub_folder in range(folder.get_number_of_sub_folders()):
        sub = folder.get_sub_folder(sub_folder)
        documents.extend(_process_pst_folder(sub, parent_name, depth + 1))

    for msg_index in range(folder.get_number_of_items()):
        message = folder.get_sub_item(msg_index)

        try:
            submitter = _safe_getattr(message, "submitter_name", "")
            sender_email = _safe_getattr(message, "sender_email_address", "")
            subject = _safe_getattr(message, "subject", "")
            body = _safe_getattr(message, "plain_text_body", "")

            if not body:
                body = _safe_getattr(message, "html_body", "")
                if body:
                    import re
                    body = re.sub(r"<[^>]+>", "", body)

            creation_time = _safe_getattr(message, "creation_time", None)
            delivery_time = _safe_getattr(message, "delivery_time", None)

            content = body.encode("utf-8") if body else b""

            doc = ExtractedDocument(
                original_filename=f"{parent_name}/{subject or 'no_subject'}.msg",
                file_extension="pst",
                mime_type="application/vnd.ms-outlook",
                file_size_bytes=len(content),
                sha256_hash=_compute_sha256(content),
                content_bytes=b"",
                author=submitter,
                sender_email=sender_email,
                sender_name=submitter,
                email_subject=subject,
                sent_date=delivery_time or creation_time,
                extracted_text=body.strip() if body else "",
            )

            attachment_count = message.get_number_of_attachments()
            doc.has_attachments = attachment_count > 0
            doc.attachment_count = attachment_count

            documents.append(doc)

        except Exception as e:
            logger.error(f"Failed to process PST message: {e}")

    return documents


def _safe_getattr(obj, attr, default=None):
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
