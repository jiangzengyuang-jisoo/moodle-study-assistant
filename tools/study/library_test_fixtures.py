"""Invented, local-only PDF/test inputs; never reads a learner's real archive."""
from pathlib import Path
import tempfile


def temporary_directory(prefix):
    root = Path(__file__).resolve().parents[2] / "tmp" / "library-tests"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=root)


def write_pdf(path, texts):
    """Write a tiny PDF using installed pypdf, with None as an image-only page."""
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject

    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=240, height=300)
        resources = DictionaryObject()
        content = DecodedStreamObject()
        if text is None:
            picture = DecodedStreamObject()
            picture.set_data(bytes([20, 80, 150]) * (20 * 20))
            picture.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                            NameObject("/Width"): NumberObject(20), NameObject("/Height"): NumberObject(20),
                            NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8)})
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im0"): writer._add_object(picture)})
            content.set_data(b"q 100 0 0 100 30 100 cm /Im0 Do Q")
        else:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject("/Helvetica")})
            resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): writer._add_object(font)})
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content.set_data(f"BT /F1 12 Tf 15 250 Td ({escaped}) Tj ET".encode("ascii"))
        page[NameObject("/Resources")] = resources
        page[NameObject("/Contents")] = writer._add_object(content)
    with Path(path).open("wb") as output:
        writer.write(output)
    writer.close()
