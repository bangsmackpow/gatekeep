import os
import logging
import tempfile
from pathlib import Path
from typing import Optional
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)


class OCRProcessor:
    def __init__(self, stirling_url: str = "http://stirling-pdf:8080", tesseract_lang: str = "eng"):
        self.stirling_url = stirling_url
        self.tesseract_lang = tesseract_lang

    def process_image(self, image_path: str) -> str:
        try:
            image = Image.open(image_path)
            text = pytesseract.image_to_string(image, lang=self.tesseract_lang)
            return text.strip()
        except Exception as e:
            logger.error(f"Tesseract OCR failed for {image_path}: {e}")
            return ""

    def process_pdf_via_stirling(self, pdf_path: str) -> str:
        import requests

        try:
            with open(pdf_path, "rb") as f:
                response = requests.post(
                    f"{self.stirling_url}/api/v1/general/ocr-pdf",
                    files={"file": (Path(pdf_path).name, f, "application/pdf")},
                    data={"language": self.tesseract_lang},
                    timeout=300,
                )
                response.raise_for_status()
                return response.text.strip()
        except requests.exceptions.RequestException as e:
            logger.error(f"Stirling-PDF OCR failed for {pdf_path}: {e}")
            return self._fallback_tesseract_pdf(pdf_path)

    def _fallback_tesseract_pdf(self, pdf_path: str) -> str:
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(pdf_path, dpi=300)
            full_text = []
            for i, image in enumerate(images):
                text = pytesseract.image_to_string(image, lang=self.tesseract_lang)
                full_text.append(f"--- Page {i+1} ---\n{text}")
            return "\n".join(full_text)
        except Exception as e:
            logger.error(f"Fallback Tesseract PDF OCR failed: {e}")
            return ""

    def needs_ocr(self, extracted_text: str, threshold: int = 50) -> bool:
        return len(extracted_text.strip()) < threshold

    def process_file(self, file_path: str, filename: str, extracted_text: str = "") -> dict:
        ext = Path(filename).suffix.lower()
        result = {"ocr_text": "", "ocr_status": "not_needed", "ocr_text_length": 0}

        if not self.needs_ocr(extracted_text):
            return result

        if ext in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif"):
            result["ocr_text"] = self.process_image(file_path)
        elif ext == ".pdf":
            result["ocr_text"] = self.process_pdf_via_stirling(file_path)
        else:
            return result

        result["ocr_status"] = "completed" if result["ocr_text"] else "failed"
        result["ocr_text_length"] = len(result["ocr_text"])
        return result
