import base64
import io

import pypdfium2 as pdfium

# Below this many characters per page the text layer is either absent or
# scanner garbage, and we go to vision instead.
MIN_CHARS_PER_PAGE = 50

# The high-res vision tier tops out at 2576px on the long edge. Past that
# costs tokens and buys nothing.
MAX_LONG_EDGE = 2400


def choose_mode(pages: list[str]) -> tuple[str, str]:
    if not pages:
        return "vision", "no pages readable as text"
    avg = sum(len(p.strip()) for p in pages) / len(pages)
    if avg < MIN_CHARS_PER_PAGE:
        return "vision", f"text layer sparse ({avg:.0f} chars/page)"
    return "text", f"text layer present ({avg:.0f} chars/page)"


def _render(page) -> str:
    long_edge = max(page.get_width(), page.get_height())
    image = page.render(scale=min(MAX_LONG_EDGE / long_edge, 4.0)).to_pil()
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return base64.standard_b64encode(buf.getvalue()).decode()


class Document:
    """A loaded PDF, already resolved to the text or the vision path.

    source_text is empty in vision mode because there is no text layer to
    ground extracted values against. Callers read it rather than
    re-deriving the rule.
    """

    def __init__(self, path: str, mode: str, reason: str, pages: list[str], images: list[str]):
        self.path = path
        self.mode = mode
        self.reason = reason
        self.pages = pages
        self.images = images

    @property
    def source_text(self) -> str:
        return "\n".join(self.pages) if self.mode == "text" else ""


def load(path: str) -> Document:
    pdf = pdfium.PdfDocument(path)
    try:
        pages = [p.get_textpage().get_text_range() for p in pdf]
        mode, reason = choose_mode(pages)
        images = [_render(p) for p in pdf] if mode == "vision" else []
    finally:
        pdf.close()
    return Document(path, mode, reason, pages, images)
