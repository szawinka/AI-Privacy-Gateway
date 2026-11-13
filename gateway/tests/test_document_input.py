# Károly Tamás szakdolgozati prototípusa (2026).
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
import unittest

from gateway.document_input import DocumentRejected, MAX_UPLOAD, extract_document, validate_text


def document(body, *, extra=None):
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>'
        ))
        archive.writestr("word/document.xml", (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>'+body+'</w:body></w:document>'
        ))
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return output.getvalue()


class DocumentInputTests(unittest.TestCase):
    def test_utf8_bom_and_exact_plain_text(self):
        self.assertEqual(extract_document("szöveg.TXT", b"\xef\xbb\xbfTeszt\r\n"), "Teszt\r\n")
        for filename, data in (("bad.txt", b"\xff"), ("file.pdf", b"pdf"), ("big.txt", b"x"*(MAX_UPLOAD+1))):
            with self.subTest(filename=filename), self.assertRaises(DocumentRejected):
                extract_document(filename, data)
        with self.assertRaises(DocumentRejected):
            validate_text("Teszt\u202eAnna")
        self.assertEqual(validate_text("\ufeffTeszt"), "\ufeffTeszt")

    def test_simple_docx_preserves_paragraph_and_cell_order(self):
        paragraph = lambda text: f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        body = paragraph("Első") + (
            "<w:tbl><w:tr><w:tc>"+paragraph("Bal")+"</w:tc><w:tc>"+
            paragraph("Jobb")+"</w:tc></w:tr></w:tbl>"
        ) + paragraph("Utolsó")
        self.assertEqual(extract_document("teszt.docx", document(body)), "Első\nBal\tJobb\nUtolsó")

    def test_unsupported_docx_content_and_external_relationship_are_rejected(self):
        bodies = (
            "<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>Titok</w:t></w:r></w:p>",
            "<w:p><w:ins><w:r><w:t>Változás</w:t></w:r></w:ins></w:p>",
            "<w:p><w:r><w:drawing/></w:r></w:p>",
            "<w:unknown/>",
            "<w:tbl><w:tr><w:tc><w:tbl/></w:tc></w:tr></w:tbl>",
        )
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(DocumentRejected):
                extract_document("bad.docx", document(body))
        relationship = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship TargetMode="External" Target="https://example.invalid"/></Relationships>'
        with self.assertRaises(DocumentRejected):
            extract_document("bad.docx", document("", extra={"word/_rels/document.xml.rels": relationship}))
        with self.assertRaises(DocumentRejected):
            extract_document("bad.docx", document("", extra={"word/header1.xml": "<header/>"}))
        with self.assertRaises(DocumentRejected):
            extract_document("bad.docx", b"not a zip")
