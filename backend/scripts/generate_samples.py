"""Generate sample PDFs used by the test-suite and for manual testing.

Usage:
    python scripts/generate_samples.py [output_dir]

Every sample is produced deterministically with PyMuPDF so the tests never
depend on binary fixtures checked into the repository.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pymupdf

PAGE_W, PAGE_H = 595, 842  # A4 in points
MARGIN = 72

LOREM = (
    "The quarterly review covers revenue, operating costs and the hiring plan "
    "for the next period. Each section summarises the most important changes "
    "and highlights the risks that the team should monitor closely."
)


def _text(page: pymupdf.Page, x: float, y: float, text: str, size: float = 11,
          font: str = "helv", color=(0, 0, 0)) -> None:
    page.insert_text((x, y), text, fontsize=size, fontname=font, color=color)


def _wrapped(page: pymupdf.Page, y: float, text: str, size: float = 11,
             font: str = "helv", width: float = PAGE_W - 2 * MARGIN,
             x: float = MARGIN, align: int = 0) -> float:
    """Insert wrapped text in a box and return the y coordinate below it."""
    rect = pymupdf.Rect(x, y, x + width, y + 400)
    rc = page.insert_textbox(rect, text, fontsize=size, fontname=font, align=align)
    used = 400 - rc if rc >= 0 else 400
    return y + used + size * 0.6


def make_text_pdf() -> bytes:
    """Headings, paragraphs, bold/italic, a bullet list, a numbered list and a link."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN
    rect = pymupdf.Rect(MARGIN, y - 10, PAGE_W - MARGIN, y + 50)
    page.insert_textbox(rect, "Annual Report 2026", fontsize=24, fontname="hebo", align=1)
    y += 50
    _text(page, MARGIN, y, "Introduction", size=16, font="hebo")
    y += 12
    y = _wrapped(page, y, LOREM)
    y = _wrapped(page, y, "This paragraph is written in italic type for emphasis.", font="heit")
    _text(page, MARGIN, y + 8, "Bold statement:", font="hebo")
    _text(page, MARGIN + 85, y + 8, "regular text continues after it.")
    y += 30
    _text(page, MARGIN, y, "Key Points", size=16, font="hebo")
    y += 22
    for item in ["Revenue grew by twelve percent", "Costs remained flat", "Two new offices opened"]:
        _text(page, MARGIN + 10, y, "•", font="helv")
        _text(page, MARGIN + 24, y, item)
        y += 16
    y += 10
    for i, item in enumerate(["Collect the data", "Review the results", "Publish the report"], 1):
        _text(page, MARGIN + 10, y, f"{i}.")
        _text(page, MARGIN + 28, y, item)
        y += 16
    y += 14
    _text(page, MARGIN, y, "Visit the project website for details.", color=(0, 0, 0.8))
    link_rect = pymupdf.Rect(MARGIN, y - 10, MARGIN + 200, y + 3)
    page.insert_link({"kind": pymupdf.LINK_URI, "from": link_rect, "uri": "https://example.com/report"})
    data = doc.tobytes()
    doc.close()
    return data


def make_table_pdf() -> bytes:
    """A ruled 4x3 table between two paragraphs."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    _text(page, MARGIN, MARGIN, "Sales by Region", size=16, font="hebo")
    y = _wrapped(page, MARGIN + 12, "The table below lists the sales figures per region.")
    rows = [["Region", "Q1", "Q2"], ["North", "120", "135"], ["South", "98", "110"], ["West", "143", "150"]]
    col_w, row_h = 120, 22
    top = y + 10
    left = MARGIN
    for r in range(len(rows) + 1):
        page.draw_line((left, top + r * row_h), (left + col_w * 3, top + r * row_h))
    for c in range(4):
        page.draw_line((left + c * col_w, top), (left + c * col_w, top + row_h * len(rows)))
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            _text(page, left + c * col_w + 6, top + r * row_h + 15, cell,
                  font="hebo" if r == 0 else "helv")
    _wrapped(page, top + row_h * len(rows) + 20, "Figures are in thousands of euros.")
    data = doc.tobytes()
    doc.close()
    return data


def make_merged_table_pdf() -> bytes:
    """A ruled table whose first row is a single cell spanning all three columns."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    left, top, col_w, row_h = MARGIN, MARGIN + 20, 120, 22
    rows = 3
    for r in range(rows + 1):
        page.draw_line((left, top + r * row_h), (left + col_w * 3, top + r * row_h))
    page.draw_line((left, top), (left, top + rows * row_h))
    page.draw_line((left + col_w * 3, top), (left + col_w * 3, top + rows * row_h))
    for c in (1, 2):  # inner verticals skip the merged first row
        page.draw_line((left + c * col_w, top + row_h), (left + c * col_w, top + rows * row_h))
    _text(page, left + 6, top + 15, "Quarterly Totals", font="hebo")
    for r, row in enumerate([["Item", "Q1", "Q2"], ["Widgets", "10", "12"]], start=1):
        for c, cell in enumerate(row):
            _text(page, left + c * col_w + 6, top + r * row_h + 15, cell)
    data = doc.tobytes()
    doc.close()
    return data


def make_multipage_pdf(pages: int = 3) -> bytes:
    """Several pages with a running header and a page-number footer."""
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        _text(page, MARGIN, 40, "Contoso Ltd - Confidential", size=9)
        _text(page, MARGIN, MARGIN + 20, f"Chapter {n}", size=18, font="hebo")
        _wrapped(page, MARGIN + 36, f"Body text for page {n}. " + LOREM)
        _text(page, PAGE_W / 2 - 20, PAGE_H - 30, f"Page {n}", size=9)
    data = doc.tobytes()
    doc.close()
    return data


def make_two_column_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    rect = pymupdf.Rect(MARGIN, MARGIN - 10, PAGE_W - MARGIN, MARGIN + 40)
    page.insert_textbox(rect, "Two Column Newsletter", fontsize=20, fontname="hebo", align=1)
    col_w = (PAGE_W - 2 * MARGIN - 20) / 2
    left_text = "LEFT COLUMN START. " + LOREM + " LEFT COLUMN END."
    right_text = "RIGHT COLUMN START. " + LOREM + " RIGHT COLUMN END."
    page.insert_textbox(pymupdf.Rect(MARGIN, MARGIN + 50, MARGIN + col_w, 500), left_text,
                        fontsize=10, fontname="helv")
    page.insert_textbox(pymupdf.Rect(MARGIN + col_w + 20, MARGIN + 50, PAGE_W - MARGIN, 500),
                        right_text, fontsize=10, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


def _sample_image_png() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80), 0)
    pix.set_rect(pix.irect, (30, 110, 200))
    pix.set_rect(pymupdf.IRect(20, 20, 100, 60), (250, 200, 40))
    return pix.tobytes("png")


def make_image_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    _text(page, MARGIN, MARGIN, "Figure Example", size=16, font="hebo")
    page.insert_image(pymupdf.Rect(MARGIN, MARGIN + 20, MARGIN + 240, MARGIN + 180),
                      stream=_sample_image_png())
    _wrapped(page, MARGIN + 200, "Figure 1: A generated sample image.")
    data = doc.tobytes()
    doc.close()
    return data


def make_scanned_pdf() -> bytes:
    """Render a text page to a bitmap and embed only the bitmap (no text layer)."""
    src = pymupdf.open()
    page = src.new_page(width=PAGE_W, height=PAGE_H)
    _text(page, MARGIN, MARGIN + 20, "Scanned Invoice", size=22, font="hebo")
    _wrapped(page, MARGIN + 50, "Thank you for your business. Payment is due within thirty days.",
             size=14)
    pix = page.get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
    src.close()
    doc = pymupdf.open()
    out = doc.new_page(width=PAGE_W, height=PAGE_H)
    out.insert_image(out.rect, stream=pix.tobytes("png"))
    data = doc.tobytes()
    doc.close()
    return data


def make_blank_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page(width=PAGE_W, height=PAGE_H)
    data = doc.tobytes()
    doc.close()
    return data


def make_encrypted_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    _text(page, MARGIN, MARGIN, "Secret")
    buf = io.BytesIO()
    doc.save(buf, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    doc.close()
    return buf.getvalue()


def make_malformed_pdf() -> bytes:
    """Starts like a PDF but the body is garbage / truncated."""
    return b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R\n" + b"\x00\xff" * 64


SAMPLES = {
    "text.pdf": make_text_pdf,
    "table.pdf": make_table_pdf,
    "merged_table.pdf": make_merged_table_pdf,
    "multipage.pdf": make_multipage_pdf,
    "two_column.pdf": make_two_column_pdf,
    "image.pdf": make_image_pdf,
    "scanned.pdf": make_scanned_pdf,
    "blank.pdf": make_blank_pdf,
    "encrypted.pdf": make_encrypted_pdf,
    "malformed.pdf": make_malformed_pdf,
}


def main(out_dir: str) -> None:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    for name, factory in SAMPLES.items():
        (target / name).write_bytes(factory())
        print(f"wrote {target / name}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2] / "samples"))
