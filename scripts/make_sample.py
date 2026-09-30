

import io
import sys
from pathlib import Path

import pypdfium2 as pdfium
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

HEADER = [
    ("Project:", "Northgate Medical Office Building - Phase 2"),
    ("Project No:", "NG-2291"),
    ("Contract No:", "C-4417"),
    ("Owner:", "Northgate Property Holdings LLC"),
    ("Contractor:", "Bellweather Construction Group"),
    ("Architect:", "Marsh & Doyle Architects"),
    ("Change Order No:", "CO-014"),
    ("Date:", "2026-03-11"),
    ("Description:", "Rock excavation and revised footing at east elevation"),
]

ROWS = [
    ("Rock excavation, east footing", 48.0, "CY", 92.50),
    ("Additional rebar, #5 bar", 1240.0, "LB", 1.35),
    ("Concrete, 4000 psi, footing", 18.5, "CY", 214.00),
    ("Crane time, mobilization", 6.0, "HR", 385.00),
    ("Dewatering pump rental", 12.0, "DAY", 145.00),
]

TAX = 0.0
MARKUP_PCT = 10.0
ORIGINAL_CONTRACT_SUM = 8_412_600.00
SCHEDULE_DAYS = 7

# A field-filled form: printed labels, handwritten values. Falls back to the
# printed face if the system has no script font, so the script still runs.
HAND_FONT_FILES = ("Inkfree.ttf", "segoesc.ttf", "segoepr.ttf", "comic.ttf")


def register_hand_font() -> str:
    for name in HAND_FONT_FILES:
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            pdfmetrics.registerFont(TTFont("Hand", str(path)))
            return "Hand"
    return "Helvetica-Oblique"


def _totals(rows):
    subtotal = round(sum(r[1] * r[3] for r in rows), 2)
    markup = round(subtotal * MARKUP_PCT / 100, 2)
    return subtotal, markup, round(subtotal + TAX + markup, 2)


def draw(path: Path, planted_error: bool, hand: bool = False) -> None:
    value_font = register_hand_font() if hand else "Helvetica"
    value_size = 11 if hand else 9
    c = canvas.Canvas(str(path), pagesize=LETTER)
    width, height = LETTER
    y = height - inch

    c.setFont("Helvetica-Bold", 15)
    c.drawString(inch, y, "CHANGE ORDER")
    c.setFont("Helvetica", 8)
    c.drawRightString(width - inch, y, "AIA Document G701 style")
    y -= 8
    c.line(inch, y, width - inch, y)
    y -= 22

    for label, value in HEADER:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(inch, y, label)
        c.setFont(value_font, value_size)
        c.drawString(inch + 1.35 * inch, y, value)
        y -= 15

    y -= 12
    c.setFont("Helvetica-Bold", 9)
    for label, x in (("DESCRIPTION", inch), ("QTY", 3.9 * inch), ("UNIT", 4.5 * inch),
                     ("UNIT PRICE", 5.35 * inch), ("EXTENDED", 6.6 * inch)):
        c.drawString(x, y, label)
    y -= 4
    c.line(inch, y, width - inch, y)
    y -= 15

    c.setFont(value_font, value_size)
    for i, (desc, qty, unit, price) in enumerate(ROWS):
        extended = round(qty * price, 2)
        if planted_error and i == 2:
            extended = 3859.00
        c.drawString(inch, y, desc)
        c.drawRightString(4.35 * inch, y, f"{qty:,.2f}")
        c.drawString(4.5 * inch, y, unit)
        c.drawRightString(6.2 * inch, y, f"{price:,.2f}")
        c.drawRightString(width - inch, y, f"{extended:,.2f}")
        y -= 14

    subtotal, markup, total = _totals(ROWS)
    y -= 6
    c.line(4.8 * inch, y, width - inch, y)
    y -= 15
    for label, amount in (
        ("Subtotal", subtotal),
        ("Sales tax", TAX),
        (f"Overhead & profit ({MARKUP_PCT:.0f}%)", markup),
    ):
        c.setFont("Helvetica", 9)
        c.drawString(4.9 * inch, y, label)
        c.setFont(value_font, value_size)
        c.drawRightString(width - inch, y, f"{amount:,.2f}")
        y -= 14

    c.setFont("Helvetica-Bold", 10)
    c.drawString(4.9 * inch, y, "TOTAL THIS CHANGE ORDER")
    c.drawRightString(width - inch, y, f"{total:,.2f}")
    y -= 22

    c.setFont("Helvetica", 9)
    c.drawString(inch, y, f"The Contract Time will be increased by {SCHEDULE_DAYS} days.")
    y -= 14
    c.drawString(inch, y, f"Original Contract Sum: {ORIGINAL_CONTRACT_SUM:,.2f}")
    y -= 14
    c.drawString(inch, y, f"Revised Contract Sum: {ORIGINAL_CONTRACT_SUM + total:,.2f}")
    y -= 30

    c.setFont("Helvetica-Bold", 9)
    c.drawString(inch, y, "NOT VALID UNTIL SIGNED BY OWNER, CONTRACTOR AND ARCHITECT")
    y -= 24
    c.setFont("Helvetica", 9)
    for role, date in (
        ("Contractor", "2026-03-12"),
        ("Architect", "2026-03-13"),
        ("Owner", "2026-03-16"),
    ):
        c.drawString(inch, y, f"{role}: ______________________________   Date: {date}")
        y -= 20

    c.save()


def flatten(src: Path, dest: Path, skew: float = 0.0) -> None:
    doc = pdfium.PdfDocument(str(src))
    try:
        image = doc[0].render(scale=2.2).to_pil()
        if skew:
            image = image.rotate(skew, expand=False, fillcolor="white")
    finally:
        doc.close()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    from reportlab.lib.utils import ImageReader

    c = canvas.Canvas(str(dest), pagesize=LETTER)
    c.drawImage(ImageReader(buf), 0, 0, width=LETTER[0], height=LETTER[1])
    c.save()


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "samples")
    out.mkdir(parents=True, exist_ok=True)

    draw(out / "sample_clean.pdf", planted_error=False)
    draw(out / "sample_flawed.pdf", planted_error=True)
    flatten(out / "sample_flawed.pdf", out / "sample_scanned.pdf")

    hand_src = out / "_handwritten_src.pdf"
    draw(hand_src, planted_error=True, hand=True)
    flatten(hand_src, out / "sample_handwritten.pdf", skew=0.8)
    hand_src.unlink()

    subtotal, markup, total = _totals(ROWS)
    print(f"correct arithmetic: subtotal {subtotal}, markup {markup}, total {total}")
    print("row 3 in sample_flawed.pdf reads 3859.00 but 18.5 x 214.00 = 3959.00")
    for name in ("sample_clean.pdf", "sample_flawed.pdf", "sample_scanned.pdf", "sample_handwritten.pdf"):
        print(f"  {out / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
