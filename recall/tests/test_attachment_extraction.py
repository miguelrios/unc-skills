from __future__ import annotations

import io
import unittest
import zipfile

from connectors.attachment_extract import extract_attachment_text
from pypdf import PdfWriter


def office_zip(member: str, xml: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, xml)
    return output.getvalue()


def minimal_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(value)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


class AttachmentExtractionTest(unittest.TestCase):
    def test_text_html_pdf_and_office_are_searchable(self) -> None:
        fixtures = {
            "text/plain": b"Synthetic plain attachment",
            "text/html": b"<h1>Synthetic HTML</h1><script>ignore()</script>",
            "application/pdf": minimal_pdf("Synthetic PDF attachment"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": office_zip(
                "word/document.xml",
                '<w:document xmlns:w="urn:w"><w:body><w:p><w:r>'
                "<w:t>Synthetic DOCX attachment</w:t></w:r></w:p></w:body></w:document>",
            ),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": office_zip(
                "ppt/slides/slide1.xml",
                '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><p:cSld><a:p><a:r>'
                "<a:t>Synthetic PPTX attachment</a:t></a:r></a:p></p:cSld></p:sld>",
            ),
        }

        results = {
            media_type: extract_attachment_text(payload, media_type)
            for media_type, payload in fixtures.items()
        }

        self.assertEqual(results["text/plain"].text, "Synthetic plain attachment")
        self.assertEqual(results["text/html"].text, "Synthetic HTML")
        self.assertIn("Synthetic PDF attachment", results["application/pdf"].text)
        self.assertEqual(
            results[
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ].text,
            "Synthetic DOCX attachment",
        )
        self.assertEqual(
            results[
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            ].text,
            "Synthetic PPTX attachment",
        )
        self.assertTrue(all(result.omissions == () for result in results.values()))

    def test_unsupported_malformed_empty_and_bomb_are_exact_failures(self) -> None:
        unsupported = extract_attachment_text(b"raw", "application/octet-stream")
        malformed = extract_attachment_text(b"not-a-pdf", "application/pdf")
        empty = extract_attachment_text(b"", "text/plain")
        bomb = office_zip(
            "word/document.xml",
            '<w:document xmlns:w="urn:w"><w:t>' + "x" * 1_100_000 + "</w:t></w:document>",
        )
        oversized_archive = extract_attachment_text(
            bomb,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        self.assertEqual(unsupported.omissions, ("attachment_unsupported_type",))
        self.assertEqual(malformed.omissions, ("attachment_malformed",))
        self.assertEqual(empty.omissions, ("attachment_empty",))
        self.assertEqual(oversized_archive.omissions, ("attachment_archive_limit",))
        self.assertTrue(all(not result.text for result in (
            unsupported, malformed, empty, oversized_archive,
        )))

    def test_encrypted_and_truncated_content_are_truthfully_partial(self) -> None:
        encrypted_output = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.encrypt("synthetic-password")
        writer.write(encrypted_output)

        encrypted = extract_attachment_text(encrypted_output.getvalue(), "application/pdf")
        truncated = extract_attachment_text(b"z" * 600_000, "text/plain")

        self.assertEqual(encrypted.omissions, ("attachment_encrypted",))
        self.assertFalse(encrypted.text)
        self.assertEqual(truncated.omissions, ("attachment_text_truncated",))
        self.assertLessEqual(len(truncated.text.encode()), 500_000)


if __name__ == "__main__":
    unittest.main()
