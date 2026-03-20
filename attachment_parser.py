"""Helpers for extracting plain text from selected attachment types."""

from __future__ import annotations

import io
import logging
import importlib
import re
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

import config

logger = logging.getLogger(__name__)


class AttachmentExtractor:
    """Extract text from safe attachment formats for LLM context."""

    _DOCX_NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}

    def __init__(self):
        self._warned_missing_pdf_parser = False
        self._pdf_reader_class = None

    def _resolve_pdf_reader(self):
        if self._pdf_reader_class is None:
            try:
                module = importlib.import_module('pypdf')
                self._pdf_reader_class = getattr(module, 'PdfReader', False)
            except Exception:
                self._pdf_reader_class = False
        if self._pdf_reader_class is False:
            return None
        return self._pdf_reader_class

    @staticmethod
    def _max_file_size() -> int:
        return max(1, int(getattr(config, 'ATTACHMENT_MAX_FILE_SIZE_BYTES', 5_242_880)))

    @staticmethod
    def _max_text_chars() -> int:
        return max(500, int(getattr(config, 'ATTACHMENT_MAX_TEXT_CHARS', 12_000)))

    @staticmethod
    def _max_files_per_email() -> int:
        return max(1, int(getattr(config, 'ATTACHMENT_MAX_FILES_PER_EMAIL', 5)))

    @staticmethod
    def _max_pdf_pages() -> int:
        return max(1, int(getattr(config, 'ATTACHMENT_MAX_PDF_PAGES', 20)))

    @staticmethod
    def _normalize_text(text: str, limit: int) -> Optional[str]:
        if not text:
            return None
        normalized = text.replace('\r\n', '\n').replace('\r', '\n')
        normalized = re.sub(r'\n{3,}', '\n\n', normalized).strip()
        if not normalized:
            return None
        if len(normalized) <= limit:
            return normalized
        truncated = normalized[:limit].rstrip()
        return f"{truncated}\n...[truncated]"

    @staticmethod
    def _decode_text_bytes(data: bytes) -> Optional[str]:
        for encoding in ('utf-8', 'utf-16', 'latin-1'):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return None

    def _extract_pdf_text(self, data: bytes) -> Optional[str]:
        pdf_reader_class = self._resolve_pdf_reader()
        if pdf_reader_class is None:
            if not self._warned_missing_pdf_parser:
                logger.warning("pypdf is not installed; PDF attachment text extraction is disabled")
                self._warned_missing_pdf_parser = True
            return None

        try:
            reader = pdf_reader_class(io.BytesIO(data))
        except Exception as exc:
            logger.warning("Failed to read PDF attachment: %s", exc)
            return None

        chunks: List[str] = []
        for page_index, page in enumerate(reader.pages):
            if page_index >= self._max_pdf_pages():
                break
            try:
                page_text = page.extract_text() or ''
            except Exception as exc:
                logger.debug("Failed extracting PDF page text: %s", exc)
                continue
            if page_text.strip():
                chunks.append(page_text.strip())
            if len('\n\n'.join(chunks)) >= self._max_text_chars():
                break

        return self._normalize_text('\n\n'.join(chunks), self._max_text_chars())

    def _extract_docx_text(self, data: bytes) -> Optional[str]:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                xml_content = archive.read('word/document.xml')
        except Exception as exc:
            logger.warning("Failed to read DOCX attachment: %s", exc)
            return None

        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as exc:
            logger.warning("Failed to parse DOCX XML: %s", exc)
            return None

        paragraphs: List[str] = []
        current_size = 0
        for paragraph in root.findall('.//w:p', self._DOCX_NS):
            fragments = [
                node.text
                for node in paragraph.findall('.//w:t', self._DOCX_NS)
                if node.text
            ]
            if not fragments:
                continue
            text = ''.join(fragments).strip()
            if not text:
                continue
            paragraphs.append(text)
            current_size += len(text) + 1
            if current_size >= self._max_text_chars():
                break

        return self._normalize_text('\n'.join(paragraphs), self._max_text_chars())

    def _extract_attachment_text(self, filename: str, content_type: str, data: bytes) -> Optional[str]:
        if not data:
            return None

        max_size = self._max_file_size()
        if len(data) > max_size:
            logger.info("Skipping attachment %s because size %s > %s", filename, len(data), max_size)
            return None

        extension = Path(filename or '').suffix.lower()
        if not extension:
            lowered_content_type = (content_type or '').lower()
            if 'pdf' in lowered_content_type:
                extension = '.pdf'
            elif 'wordprocessingml' in lowered_content_type:
                extension = '.docx'

        if extension in {'.txt', '.md', '.csv', '.log'}:
            text = self._decode_text_bytes(data)
            return self._normalize_text(text or '', self._max_text_chars())

        if extension == '.pdf':
            return self._extract_pdf_text(data)

        if extension == '.docx':
            return self._extract_docx_text(data)

        return None

    @staticmethod
    def _format_sections(sections: List[Tuple[str, str]]) -> str:
        if not sections:
            return ''

        lines: List[str] = ["--- Attachment Text Extracts (auto) ---"]
        for name, text in sections:
            lines.append(f"[Attachment: {name}]")
            lines.append(text)
            lines.append('')
        return '\n'.join(lines).strip()

    def extract_from_mime_message(self, msg) -> str:
        if not getattr(config, 'ATTACHMENT_PARSING_ENABLED', True):
            return ''
        if not msg or not msg.is_multipart():
            return ''

        sections: List[Tuple[str, str]] = []
        max_files = self._max_files_per_email()

        for part in msg.walk():
            if len(sections) >= max_files:
                break

            disposition = str(part.get('Content-Disposition') or '')
            if 'attachment' not in disposition.lower():
                continue

            filename = part.get_filename() or 'attachment'
            data = part.get_payload(decode=True)
            if not data:
                continue

            text = self._extract_attachment_text(filename, part.get_content_type(), data)
            if not text:
                continue

            sections.append((filename, text))

        return self._format_sections(sections)

    def extract_from_exchange_item(self, item) -> str:
        if not getattr(config, 'ATTACHMENT_PARSING_ENABLED', True):
            return ''
        if not item:
            return ''

        sections: List[Tuple[str, str]] = []
        max_files = self._max_files_per_email()

        for attachment in (getattr(item, 'attachments', None) or []):
            if len(sections) >= max_files:
                break

            filename = (getattr(attachment, 'name', None) or 'attachment').strip() or 'attachment'
            content_type = getattr(attachment, 'content_type', '') or ''

            data = getattr(attachment, 'content', None)
            if data is None:
                try:
                    if hasattr(attachment, 'load'):
                        attachment.load()
                    data = getattr(attachment, 'content', None)
                except Exception as exc:
                    logger.debug("Failed loading Exchange attachment %s: %s", filename, exc)
                    continue

            if not isinstance(data, (bytes, bytearray)):
                continue

            text = self._extract_attachment_text(filename, content_type, bytes(data))
            if not text:
                continue

            sections.append((filename, text))

        return self._format_sections(sections)
