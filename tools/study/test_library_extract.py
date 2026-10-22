"""Semantic extraction tests use synthetic temporary documents only."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from learning_library.extract import EXTRACTOR_VERSION, extract_document
from library_test_fixtures import temporary_directory, write_pdf

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory("extract-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(Path(__file__).resolve().parents[2] / "tmp"))
        self.tmp.cleanup()

    def package(self, name, parts):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            for part, data in parts.items():
                archive.writestr(part, data)
        return path

    def assert_unverified(self, result):
        self.assertEqual(result["extractor_version"], EXTRACTOR_VERSION)
        self.assertFalse(result["verified"])
        for page in result["pages"]:
            self.assertFalse(page["verified"])
            self.assertIn("needs_visual_review", page["flags"])

    def test_pptx_uses_relationship_order_keeps_tables_and_notes_separate(self):
        slide = f'''<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><p:cSld><p:spTree>
          <p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>First in presentation</a:t></a:r></a:p></p:txBody></p:sp>
          <a:tbl><a:tr><a:tc><a:txBody><a:p><a:r><a:t>row one</a:t></a:r></a:p></a:txBody></a:tc><a:tc><a:txBody><a:p><a:r><a:t>value one</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl>
          <p:pic/><m:oMath><m:r><m:t>x=2</m:t></m:r></m:oMath>
          </p:spTree></p:cSld></p:sld>'''
        parts = {
            "ppt/presentation.xml": f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst><p:sldId id="300" r:id="rB"/><p:sldId id="301" r:id="rA"/></p:sldIdLst></p:presentation>',
            "ppt/_rels/presentation.xml.rels": f'<Relationships xmlns="{REL}"><Relationship Id="rA" Type="{R}/slide" Target="slides/slide1.xml"/><Relationship Id="rB" Type="{R}/slide" Target="slides/slide20.xml"/></Relationships>',
            "ppt/slides/slide20.xml": slide,
            "ppt/slides/slide1.xml": f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Second only</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>',
            "ppt/slides/_rels/slide20.xml.rels": f'<Relationships xmlns="{REL}"><Relationship Id="notes" Type="{R}/notesSlide" Target="../notesSlides/notesSlide9.xml"/></Relationships>',
            "ppt/notesSlides/notesSlide9.xml": f'<p:notes xmlns:p="{P}" xmlns:a="{A}"><a:p><a:r><a:t>SECRET SPEAKER NOTE</a:t></a:r></a:p></p:notes>',
        }
        path = self.package("ordered.pptx", parts)
        original = hashlib.sha256(path.read_bytes()).hexdigest()
        result = extract_document(path)
        self.assertEqual(len(result["pages"]), 2)
        first, second = result["pages"]
        self.assertEqual(first["heading"], "First in presentation")
        self.assertIn("row one\tvalue one", first["text"])
        self.assertIn("x=2", first["text"])
        self.assertNotIn("SECRET", first["text"])
        self.assertEqual(first["speaker_notes"], "SECRET SPEAKER NOTE")
        self.assertEqual(second["text"], "Second only")
        self.assertEqual([p["page"] for p in result["pages"]], [1, 2])
        self.assertIn("contains_images_or_drawings", first["flags"])
        self.assertIn("contains_formula", first["flags"])
        self.assertEqual(original, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assert_unverified(result)

    def test_docx_structural_sections_and_table_do_not_fake_pages(self):
        path = self.package("source.docx", {"word/document.xml": f'''<w:document xmlns:w="{W}"><w:body>
          <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Actual heading</w:t></w:r></w:p>
          <w:p><w:r><w:t>Paragraph one</w:t><w:br w:type="page"/><w:t>after explicit break</w:t></w:r></w:p>
          <w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:sectPr/>
          </w:body></w:document>'''})
        result = extract_document(path)
        self.assertEqual(result["unit_kind"], "section")
        self.assertEqual(len(result["pages"]), 3)
        self.assertIn("after explicit break", result["pages"][1]["text"])
        self.assertIn("explicit_page_break_marker", result["pages"][1]["flags"])
        self.assertEqual(result["pages"][2]["text"], "A\tB")
        self.assertTrue(all("not_physical_page" in p["flags"] for p in result["pages"]))

    def test_html_skips_executable_and_style_text_preserves_headings(self):
        path = self.root / "notes.html"
        path.write_text('<html><head><title>Not body</title><style>hidden CSS</style></head><body><h1>Heading A</h1><p>A &amp; B</p><script>ignore me</script><h2>Heading B</h2><p>Next</p><img src="https://invalid.example/x.png"></body></html>', encoding="utf-8")
        result = extract_document(path)
        self.assertEqual(len(result["pages"]), 2)
        self.assertEqual(result["pages"][1]["heading"], "Heading B")
        full = "\n".join(p["text"] for p in result["pages"])
        self.assertIn("A & B", full)
        for banned in ("ignore me", "hidden CSS", "Not body"):
            self.assertNotIn(banned, full)
        self.assertIn("contains_images_or_embedded_content", result["pages"][1]["flags"])

    def test_pdf_physical_pages_and_empty_scan_are_not_lost(self):
        path = self.root / "pages.pdf"
        write_pdf(path, ["First physical page", None, "Third physical page"])
        result = extract_document(path)
        self.assertEqual(result["unit_kind"], "pdf_page")
        self.assertEqual(len(result["pages"]), 3)
        self.assertEqual(result["pages"][1]["text"], "")
        self.assertIn("needs_ocr", result["pages"][1]["flags"])
        self.assertIn("possible_scan_or_blank_page", result["pages"][1]["flags"])
        self.assertIn("Third", result["pages"][2]["text"])
        self.assert_unverified(result)

    def test_image_and_archive_do_not_invent_content(self):
        from PIL import Image
        path = self.root / "image.jpg"
        Image.new("RGB", (10, 12), "white").save(path)
        result = extract_document(path)
        self.assertEqual(result["pages"][0]["text"], "")
        self.assertEqual(result["pages"][0]["dimensions"], [10, 12])
        self.assertIn("needs_ocr", result["pages"][0]["flags"])
        archive = self.package("archive.zip", {"../escape.txt": "DO NOT EXTRACT"})
        result = extract_document(archive)
        self.assertEqual(result["pages"], [])
        self.assertIn("unsupported_archive", result["warnings"])
        self.assertFalse((self.root.parent / "escape.txt").exists())

    def test_corrupt_documents_return_failure_and_no_partial_content(self):
        for suffix in ("pptx", "docx", "pdf", "jpg"):
            with self.subTest(suffix=suffix):
                path = self.root / ("broken." + suffix)
                path.write_bytes(b"not a valid document")
                result = extract_document(path)
                self.assertEqual(result["pages"], [])
                self.assertIn("extraction_failed", result["warnings"])

    def test_missing_file_is_explicit(self):
        result = extract_document(self.root / "absent.pdf")
        self.assertIn("source_missing_or_not_file", result["warnings"])

    def test_poppler_fallback_preserves_blank_middle_page(self):
        from learning_library import extract
        import subprocess
        result = extract._result("pdf", "pdf_page")
        proc = subprocess.CompletedProcess([], 0, b"first\f\fthird\f", b"")
        with patch.object(extract.shutil, "which", return_value="pdftotext"), patch.object(extract.subprocess, "run", return_value=proc):
            extract._pdf_poppler(self.root / "fake.pdf", result)
        self.assertEqual([p["text"] for p in result["pages"]], ["first", "", "third"])

    def test_xml_entity_declarations_rejected(self):
        path = self.package("entity.docx", {"word/document.xml": '<!DOCTYPE x [<!ENTITY x "expanded">]><x>&x;</x>'})
        result = extract_document(path)
        self.assertIn("unsafe_xml_declaration", result["warnings"])
        self.assertFalse(result["pages"])

    def test_invalid_later_slide_cannot_return_partial_deck_as_complete(self):
        path = self.package("missing-slide.pptx", {
            "ppt/presentation.xml": f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst><p:sldId r:id="ok"/><p:sldId r:id="missing"/></p:sldIdLst></p:presentation>',
            "ppt/_rels/presentation.xml.rels": f'<Relationships xmlns="{REL}"><Relationship Id="ok" Type="{R}/slide" Target="slides/slide1.xml"/></Relationships>',
            "ppt/slides/slide1.xml": f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><a:p><a:r><a:t>Partial content</a:t></a:r></a:p></p:sld>',
        })
        result = extract_document(path)
        self.assertEqual(result["pages"], [])
        self.assertIn("invalid_slide_relationship", result["warnings"])

    def test_password_protected_pdf_is_explicit(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf unavailable")
        path = self.root / "locked.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt("test-only-password")
        with path.open("wb") as output:
            writer.write(output)
        result = extract_document(path)
        self.assertEqual(result["pages"], [])
        self.assertIn("encrypted_pdf", result["warnings"])

    def test_chart_text_is_not_claimed_complete(self):
        from learning_library import extract
        import xml.etree.ElementTree as ET
        chart = ET.fromstring('<root xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart/></root>')
        self.assertIn("contains_chart", extract._visual_flags(chart))


if __name__ == "__main__":
    unittest.main()
