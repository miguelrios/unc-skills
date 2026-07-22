"""Bounded, provider-neutral text extraction for privately archived attachments."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser

MAX_ATTACHMENT_BYTES = 16 * 1024 * 1024
MAX_EXTRACTED_TEXT_BYTES = 500_000
MAX_OFFICE_MEMBERS = 256
MAX_OFFICE_MEMBER_BYTES = 1_000_000
MAX_OFFICE_TOTAL_BYTES = 8_000_000
MAX_COMPRESSION_RATIO = 200
MAX_PDF_PAGES = 200
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SUPPORTED_MEDIA_TYPES = frozenset({"text/plain", "text/html", "application/pdf", DOCX, PPTX})


@dataclass(frozen=True)
class AttachmentExtraction:
    text: str = ""
    omissions: tuple[str, ...] = ()


class _HTMLText(HTMLParser):
    _BLOCKS = {"br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p", "tr"}
    _HIDDEN = {"head", "script", "style", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in self._HIDDEN:
            self.hidden += 1
        elif not self.hidden and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self._HIDDEN:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _normalize(value: str) -> str:
    return "\n".join(
        line for raw in value.splitlines()
        if (line := re.sub(r"[\t\f\v ]+", " ", raw).strip())
    )


def _bounded(value: str) -> AttachmentExtraction:
    normalized = _normalize(value)
    if not normalized:
        return AttachmentExtraction(omissions=("attachment_no_extractable_text",))
    encoded = normalized.encode()
    if len(encoded) <= MAX_EXTRACTED_TEXT_BYTES:
        return AttachmentExtraction(text=normalized)
    bounded = encoded[:MAX_EXTRACTED_TEXT_BYTES].decode(errors="ignore")
    return AttachmentExtraction(
        text=bounded,
        omissions=("attachment_text_truncated",),
    )


def _html(payload: bytes) -> AttachmentExtraction:
    try:
        value = payload.decode("utf-8")
        parser = _HTMLText()
        parser.feed(value)
        parser.close()
    except (UnicodeDecodeError, ValueError, AssertionError):
        return AttachmentExtraction(omissions=("attachment_malformed",))
    return _bounded("".join(parser.parts))


def _pdf(payload: bytes) -> AttachmentExtraction:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        return AttachmentExtraction(omissions=("attachment_extractor_unavailable",))
    try:
        reader = PdfReader(io.BytesIO(payload), strict=False)
        if reader.is_encrypted:
            return AttachmentExtraction(omissions=("attachment_encrypted",))
        if len(reader.pages) > MAX_PDF_PAGES:
            return AttachmentExtraction(omissions=("attachment_page_limit",))
        return _bounded("\n\n".join(page.extract_text() or "" for page in reader.pages))
    except (PdfReadError, ValueError, TypeError, KeyError, OSError):
        return AttachmentExtraction(omissions=("attachment_malformed",))


def _office(payload: bytes, media_type: str) -> AttachmentExtraction:
    try:
        from defusedxml import ElementTree
    except ImportError:
        return AttachmentExtraction(omissions=("attachment_extractor_unavailable",))
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (OSError, zipfile.BadZipFile):
        return AttachmentExtraction(omissions=("attachment_malformed",))
    try:
        with archive:
            members = archive.infolist()
            if len(members) > MAX_OFFICE_MEMBERS:
                return AttachmentExtraction(omissions=("attachment_archive_limit",))
            total = 0
            selected = []
            for info in members:
                total += info.file_size
                ratio = info.file_size / max(info.compress_size, 1)
                if (
                    info.flag_bits & 0x1
                    or info.file_size > MAX_OFFICE_MEMBER_BYTES
                    or total > MAX_OFFICE_TOTAL_BYTES
                    or ratio > MAX_COMPRESSION_RATIO
                ):
                    return AttachmentExtraction(omissions=("attachment_archive_limit",))
                name = info.filename
                if media_type == DOCX:
                    wanted = (
                        name == "word/document.xml"
                        or name.startswith("word/header")
                        or name.startswith("word/footer")
                        or name == "word/comments.xml"
                    )
                else:
                    wanted = (
                        name.startswith("ppt/slides/slide")
                        or name.startswith("ppt/notesSlides/notesSlide")
                    )
                if wanted and name.endswith(".xml"):
                    selected.append(info)
            if not selected:
                return AttachmentExtraction(omissions=("attachment_malformed",))
            parts = []
            for info in sorted(selected, key=lambda item: item.filename):
                root = ElementTree.fromstring(archive.read(info))
                values = [
                    element.text
                    for element in root.iter()
                    if element.tag.rsplit("}", 1)[-1] == "t" and element.text
                ]
                if values:
                    parts.append(" ".join(values))
            return _bounded("\n\n".join(parts))
    except (OSError, RuntimeError, zipfile.BadZipFile, ElementTree.ParseError):
        return AttachmentExtraction(omissions=("attachment_malformed",))


def extract_attachment_text(payload: bytes, media_type: str) -> AttachmentExtraction:
    """Return bounded searchable text or one stable, content-free omission class."""
    if not isinstance(payload, bytes) or not isinstance(media_type, str):
        return AttachmentExtraction(omissions=("attachment_malformed",))
    if media_type not in SUPPORTED_MEDIA_TYPES:
        return AttachmentExtraction(omissions=("attachment_unsupported_type",))
    if not payload:
        return AttachmentExtraction(omissions=("attachment_empty",))
    if len(payload) > MAX_ATTACHMENT_BYTES:
        return AttachmentExtraction(omissions=("attachment_size_limit",))
    if media_type == "text/plain":
        try:
            return _bounded(payload.decode("utf-8"))
        except UnicodeDecodeError:
            return AttachmentExtraction(omissions=("attachment_malformed",))
    if media_type == "text/html":
        return _html(payload)
    if media_type == "application/pdf":
        return _pdf(payload)
    return _office(payload, media_type)
