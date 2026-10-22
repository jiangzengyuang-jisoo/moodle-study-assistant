"""Render a physical original PDF page when Poppler is not installed.

Uses the project's optional pypdfium2/Pillow runtime. Text, diagrams,
annotations and form appearances are rendered together; the result is still
an unverified view of the source. No OCR, answer generation or source edits.
"""
from pathlib import Path


def render_pdfium(source, unit, dpi, output):
    if isinstance(unit, bool) or not isinstance(unit, int) or unit < 1:
        raise ValueError("PDF page number must be a positive integer")
    if not isinstance(dpi, (int, float)) or not 72 <= dpi <= 300:
        raise ValueError("PDF rendering DPI must be between 72 and 300")
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ValueError("Rendered output cannot overwrite the original PDF")
    try:
        import pypdfium2 as pdfium
        from PIL import Image  # noqa: F401: required by PdfBitmap.to_pil()
    except ImportError as error:
        raise ValueError("PDF rendering requires pypdfium2 and Pillow in the project environment, or Poppler pdftoppm on PATH. Run setup again to install project dependencies.") from error
    document = pdfium.PdfDocument(str(source))
    page = bitmap = image = None
    try:
        if unit > len(document):
            raise ValueError("PDF page number exceeds the source page count")
        document.init_forms()
        page = document[unit - 1]
        bitmap = page.render(scale=dpi / 72, draw_annots=True, may_draw_forms=True)
        image = bitmap.to_pil()
        image.save(str(output), format="PNG")
    finally:
        if image is not None:
            image.close()
        if bitmap is not None:
            bitmap.close()
        if page is not None:
            page.close()
        document.close()
