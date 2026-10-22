"""Read-only, source-local text extraction. Extracted text is never verification.

``page`` is one-based: a physical PDF page, presentation-order slide, or a
structural section for DOCX/HTML. The latter must not be cited as physical pages.
No files are changed, attachments executed, archives unpacked, or URLs fetched.
"""
from __future__ import annotations

from html.parser import HTMLParser
import importlib
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

EXTRACTOR_VERSION = "1.0.0"
_MAX_XML = 32 * 1024 * 1024
_MAX_PACKAGE = 512 * 1024 * 1024
_MATH = re.compile(r"[∫∑∏√≈≠≤≥∞∂∇∈∉∀∃]|\b(?:sin|cos|tan|lim)\s*\(|[A-Za-z0-9)]\s*[=^]\s*[-+(A-Za-z0-9]")
_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


class ExtractionError(Exception):
    pass


def _tag(node):
    return node.tag.rsplit("}", 1)[-1]


def _unique(values):
    return list(dict.fromkeys(values))


def _clean(value):
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()


def _unit(number, text, heading="", flags=(), **extra):
    text = _clean(text)
    flags = ["text_extraction_unverified", "needs_visual_review", *flags]
    if not text:
        flags.append("no_extractable_text")
    if _MATH.search(text):
        flags.append("possible_formula")
    if "\ufffd" in text:
        flags.append("replacement_characters")
    return {"page": number, "text": text, "heading": _clean(heading)[:240],
            "flags": _unique(flags), "verified": False, **extra}


def _result(fmt, kind):
    return {"format": fmt, "unit_kind": kind, "pages": [],
            "warnings": ["text_extraction_is_not_source_verification"],
            "extractor_version": EXTRACTOR_VERSION, "verified": False}


def _heading(text):
    return next((line.strip() for line in text.splitlines() if line.strip()), "")[:240]


def _xml(archive, name):
    info = archive.getinfo(name)
    if info.file_size > _MAX_XML:
        raise ExtractionError("xml_size_limit")
    raw = archive.read(info)
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
        raise ExtractionError("unsafe_xml_declaration")
    return ET.fromstring(raw)


def _package(path):
    archive = zipfile.ZipFile(path)
    entries = archive.infolist()
    if len(entries) > 20000 or sum(x.file_size for x in entries) > _MAX_PACKAGE:
        archive.close()
        raise ExtractionError("package_size_limit")
    if len({x.filename for x in entries}) != len(entries):
        archive.close()
        raise ExtractionError("duplicate_package_parts")
    return archive


def _relationships(archive, part):
    p = PurePosixPath(part)
    name = str(p.parent / "_rels" / (p.name + ".rels"))
    if name not in archive.namelist():
        return {}
    result = {}
    for rel in _xml(archive, name):
        if _tag(rel) != "Relationship":
            continue
        rid = rel.get("Id")
        if not rid or rid in result:
            raise ExtractionError("invalid_relationship_ids")
        target = rel.get("Target", "")
        external = rel.get("TargetMode") == "External" or bool(urlsplit(target).scheme)
        resolved = None
        if not external:
            target = unquote(target).replace("\\", "/")
            resolved = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                                         else str(p.parent / target))
            if resolved.startswith("../") or resolved == "..":
                raise ExtractionError("invalid_relationship_target")
        result[rid] = {"target": resolved, "type": rel.get("Type", ""), "external": external}
    return result


def _runs(node):
    parts = []
    for child in node.iter():
        tag = _tag(child)
        if tag == "t" and child.text:
            parts.append(child.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag in ("br", "cr"):
            parts.append("\n")
    return "".join(parts)


def _xml_text(node):
    """Preserve paragraph and table-cell boundaries, without duplicate text."""
    tag = _tag(node)
    if tag == "tbl":
        return "\n".join("\t".join(_xml_text(cell) for cell in row if _tag(cell) == "tc")
                         for row in node if _tag(row) == "tr")
    if tag == "p":
        return _runs(node)
    if tag in ("oMath", "oMathPara"):
        return _runs(node)
    return "\n".join(value for child in node if (value := _xml_text(child)))


def _visual_flags(node):
    tags = {_tag(n) for n in node.iter()}
    flags = ["layout_not_preserved"]
    if tags & {"pic", "blip", "imagedata", "drawing", "pict"}:
        flags.append("contains_images_or_drawings")
    if "chart" in tags:
        flags.append("contains_chart")
    if "tbl" in tags:
        flags.append("contains_table")
    if tags & {"oMath", "oMathPara"}:
        flags.append("contains_formula")
    if tags & {"relIds", "oleObj", "object", "AlternateContent"}:
        flags.append("contains_embedded_or_alternate_content")
    return flags


def _pdf(path, result):
    reader_class = None
    for package in ("pypdf", "PyPDF2"):
        try:
            reader_class = importlib.import_module(package).PdfReader
            break
        except ImportError:
            pass
    if reader_class is None:
        return _pdf_poppler(path, result)
    try:
        with path.open("rb") as stream:
            reader = reader_class(stream, strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise ExtractionError("encrypted_pdf")
            result["backend"] = reader_class.__module__.split(".")[0]
            for index, page in enumerate(reader.pages, 1):
                flags = ["layout_not_preserved", "visual_content_not_fully_detectable"]
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                    flags.append("page_text_extraction_failed")
                    result["warnings"].append("partial_text_extraction")
                    result["warnings"].append(f"page_{index}_text_extraction_failed")
                try:
                    resources = page.get("/Resources", {})
                    if hasattr(resources, "get_object"):
                        resources = resources.get_object()
                    xobjects = resources.get("/XObject", {})
                    if hasattr(xobjects, "get_object"):
                        xobjects = xobjects.get_object()
                    subtypes = {obj.get_object().get("/Subtype") for obj in xobjects.values()}
                    if "/Image" in subtypes:
                        flags.append("contains_images_or_drawings")
                    if "/Form" in subtypes:
                        flags.append("contains_form_xobject")
                    if not text.strip():
                        flags.append("possible_scan_or_blank_page")
                        if subtypes & {"/Image", "/Form"}:
                            flags.append("needs_ocr")
                except Exception:
                    flags.append("visual_metadata_unavailable")
                result["pages"].append(_unit(index, text, _heading(text), flags))
    except ExtractionError:
        raise
    except Exception:
        if shutil.which("pdftotext"):
            result["pages"] = []
            result["warnings"].append("primary_pdf_parser_failed")
            return _pdf_poppler(path, result)
        raise ExtractionError("pdf_parse_failed") from None
    return result


def _pdf_poppler(path, result):
    command = shutil.which("pdftotext")
    if not command:
        raise ExtractionError("pdf_extractor_unavailable")
    proc = subprocess.run([command, "-enc", "UTF-8", "-layout", str(path.resolve()), "-"],
                          capture_output=True, timeout=120, check=False)
    if proc.returncode:
        raise ExtractionError("pdf_parse_failed")
    text = proc.stdout.decode("utf-8", errors="replace")
    pages = text.split("\f")
    if pages and not pages[-1].strip() and "\f" in text:
        pages.pop()  # pdftotext terminates the final physical page with form-feed.
    result["backend"] = "pdftotext"
    result["warnings"].append("pdf_visual_metadata_unavailable")
    for index, content in enumerate(pages, 1):
        flags = ["layout_not_preserved", "visual_content_not_fully_detectable"]
        if not content.strip():
            flags.append("possible_scan_or_blank_page")
        result["pages"].append(_unit(index, content, _heading(content), flags))
    return result


def _pptx(path, result):
    with _package(path) as archive:
        presentation = _xml(archive, "ppt/presentation.xml")
        relationships = _relationships(archive, "ppt/presentation.xml")
        slide_ids = presentation.find("p:sldIdLst", _NS)
        if slide_ids is None:
            raise ExtractionError("missing_slide_order")
        used = set()
        for number, slide_id in enumerate(slide_ids, 1):
            rid = slide_id.get("{" + _NS["r"] + "}id")
            rel = relationships.get(rid)
            if not rel or rel["external"] or not rel["type"].endswith("/slide"):
                raise ExtractionError("invalid_slide_relationship")
            part = rel["target"]
            if part in used:
                raise ExtractionError("duplicate_slide_reference")
            used.add(part)
            slide = _xml(archive, part)
            text = _xml_text(slide)
            title = ""
            for shape in slide.findall(".//p:sp", _NS):
                ph = shape.find(".//p:ph", _NS)
                if ph is not None and ph.get("type") in ("title", "ctrTitle"):
                    title = _xml_text(shape)
                    break
            flags = _visual_flags(slide)
            notes = []
            for relation in _relationships(archive, part).values():
                if relation["external"]:
                    flags.append("external_relationship_not_followed")
                elif relation["type"].endswith("/notesSlide"):
                    note = _xml(archive, relation["target"])
                    notes.append(_xml_text(note))
            notes_text = "\n".join(notes)
            if notes_text.strip():
                flags.append("speaker_notes_separate")
            result["pages"].append(_unit(number, text, title or _heading(text), flags,
                source_part=part, speaker_notes=_clean(notes_text), speaker_notes_verified=False))
    result["backend"] = "stdlib_ooxml"
    result["warnings"].append("slide_visuals_and_master_layout_require_review")
    return result


def _docx(path, result):
    with _package(path) as archive:
        document = _xml(archive, "word/document.xml")
        body = document.find("w:body", _NS)
        if body is None:
            raise ExtractionError("missing_document_body")
        heading = ""
        for block in body:
            if _tag(block) == "sectPr":
                continue
            text = _xml_text(block)
            style_node = block.find("w:pPr/w:pStyle", _NS)
            style = style_node.get("{" + _NS["w"] + "}val", "") if style_node is not None else ""
            if re.match(r"(?:heading\d*|title|标题\d*)$", style, re.I) or block.find("w:pPr/w:outlineLvl", _NS) is not None:
                heading = _heading(text)
            flags = [*_visual_flags(block), "not_physical_page"]
            if any(_tag(n) == "br" and n.get("{" + _NS["w"] + "}type") == "page" for n in block.iter()):
                flags.append("explicit_page_break_marker")
            result["pages"].append(_unit(len(result["pages"]) + 1, text, heading, flags,
                structure_kind=_tag(block), source_part="word/document.xml"))
        extras = sorted(name for name in archive.namelist()
                        if re.fullmatch(r"word/(?:footnotes|endnotes|header\d+|footer\d+)\.xml", name))
        for part in extras:
            node = _xml(archive, part)
            text = _xml_text(node)
            if text.strip():
                result["pages"].append(_unit(len(result["pages"]) + 1, text, PurePosixPath(part).stem,
                    [*_visual_flags(node), "not_physical_page", "ancillary_text"],
                    structure_kind="ancillary", source_part=part))
    result["backend"] = "stdlib_ooxml"
    result["warnings"].append("section_numbers_are_structural_not_physical_pages")
    return result


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = []
        self.sections = []
        self.parts = []
        self.heading = ""
        self.in_heading = None
        self.heading_parts = []
        self.flags = []

    def flush(self):
        value = _clean("".join(self.parts))
        if value or self.flags:
            self.sections.append((value, self.heading, self.flags[:]))
        self.parts = []
        self.flags = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.skip:
            if tag in {"script", "style", "head", "template", "noscript"}:
                self.skip.append(tag)
            return
        if tag in {"script", "style", "head", "template", "noscript"}:
            self.skip.append(tag)
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.in_heading = tag
            self.heading_parts = []
        if tag in {"p", "div", "li", "section", "article", "tr", "br", "hr"}:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append("\t")
        if tag in {"img", "svg", "canvas", "video", "object", "iframe"}:
            self.flags.append("contains_images_or_embedded_content")
        if tag == "table":
            self.flags.append("contains_table")
        if tag == "math":
            self.flags.append("contains_formula")
        if tag == "a" and attrs.get("href"):
            self.flags.append("linked_content_not_fetched")

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag == self.in_heading:
            self.heading = _clean("".join(self.heading_parts))
            self.parts.append("\n")
            self.in_heading = None
        elif tag in {"p", "div", "li", "section", "article", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip:
            return
        self.parts.append(data)
        if self.in_heading:
            self.heading_parts.append(data)


def _html(path, result):
    raw = path.read_bytes()
    match = re.search(br"charset\s*=\s*[\"']?\s*([\w-]+)", raw[:8192], re.I)
    encoding = ("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else
                match.group(1).decode("ascii") if match else "utf-8-sig")
    try:
        text = raw.decode(encoding, errors="replace")
    except LookupError:
        text = raw.decode("utf-8-sig", errors="replace")
        result["warnings"].append("unknown_html_encoding")
    parser = _HTMLText()
    parser.feed(text)
    parser.close()
    parser.flush()
    result["pages"] = [_unit(i, text, heading, [*flags, "not_physical_page", "layout_not_preserved"])
                       for i, (text, heading, flags) in enumerate(parser.sections, 1)]
    result["backend"] = "stdlib_html"
    result["warnings"].extend(["section_numbers_are_structural_not_physical_pages", "scripts_and_styles_not_executed"])
    return result


def extract_document(path: Path) -> dict:
    """Return a JSON-serializable extraction, with recoverable health warnings.

    ``extraction_failed`` means no content should be used. A partial parse is
    cleared on failure rather than mistaken for a complete document.
    """
    path = Path(path)
    fmt = path.suffix.lower().lstrip(".")
    kind = {"pdf": "pdf_page", "pptx": "slide", "jpg": "image", "jpeg": "image",
            "png": "image", "gif": "image", "webp": "image"}.get(fmt, "section")
    result = _result(fmt or "unknown", kind)
    try:
        if not path.is_file():
            raise ExtractionError("source_missing_or_not_file")
        if fmt == "pdf":
            _pdf(path, result)
        elif fmt == "pptx":
            _pptx(path, result)
        elif fmt == "docx":
            _docx(path, result)
        elif fmt in {"html", "htm"}:
            _html(path, result)
        elif kind == "image":
            dimensions = None
            try:
                from PIL import Image
                with Image.open(path) as picture:
                    dimensions = list(picture.size)
                    picture.verify()
            except ImportError:
                result["warnings"].append("image_validation_unavailable")
            except Exception:
                raise ExtractionError("image_parse_failed") from None
            result["pages"] = [_unit(1, "", "", ["needs_ocr", "image_text_not_extracted"], dimensions=dimensions)]
            result["warnings"].append("ocr_not_performed")
        elif fmt in {"zip", "7z", "rar", "tar", "gz"}:
            result["warnings"].append("unsupported_archive")
        elif fmt in {"txt", "md"}:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            result["pages"] = [_unit(1, text, _heading(text), ["not_physical_page"])]
        else:
            result["warnings"].append("unsupported_format")
        if not result["pages"]:
            result["warnings"].append("no_extracted_units")
    except ExtractionError as error:
        result["pages"] = []
        result["warnings"].extend(["extraction_failed", str(error)])
    except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile, RuntimeError, subprocess.TimeoutExpired):
        result["pages"] = []
        result["warnings"].extend(["extraction_failed", "document_parse_failed"])
    result["warnings"] = _unique(result["warnings"])
    return result
