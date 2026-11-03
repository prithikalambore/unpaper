"""Per-page extraction: text spans, links, raster images, tables and OCR."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pymupdf

from . import ocr
from .model import BBox, Image, Line, Table
from .spans import SAFE_LINK_SCHEMES, RawSpan, link_for, span_to_run, spans_to_lines
from .tables import detect_tables

log = logging.getLogger(__name__)

SCANNED_MAX_CHARS = 20  # pages with fewer real characters may be scans
SCANNED_MIN_COVERAGE = 0.3  # ...if images cover at least this share of the page
MIN_IMAGE_PT = 6  # ignore hairline/decoration images
MAX_IMAGE_DPI = 300


@dataclass
class RawPage:
    number: int
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    images: list[Image] = field(default_factory=list)
    ocr_used: bool = False
    looks_scanned: bool = False
    ocr_found_nothing: bool = False
    skipped_rotated: int = 0

    @property
    def char_count(self) -> int:
        n = sum(len(ln.text.strip()) for ln in self.lines)
        for t in self.tables:
            for row in t.rows:
                for cell in row:
                    if cell:
                        n += sum(len(r.text.strip()) for p in cell.paragraphs for r in p)
        return n


def _area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _clip(b: BBox, page: pymupdf.Rect) -> BBox:
    return (max(b[0], page.x0), max(b[1], page.y0), min(b[2], page.x1), min(b[3], page.y1))


def _page_links(page: pymupdf.Page) -> list[tuple[BBox, str]]:
    links = []
    for link in page.get_links():
        uri = (link.get("uri") or "").strip()
        if link.get("kind") == pymupdf.LINK_URI and uri.lower().startswith(SAFE_LINK_SCHEMES):
            links.append((tuple(link["from"]), uri))
    return links


def _collect_spans(textdict: dict, links: list[tuple[BBox, str]]) -> tuple[list[RawSpan], int]:
    spans: list[RawSpan] = []
    rotated = 0
    line_id = 0
    for block in textdict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            line_id += 1
            dx, dy = line.get("dir", (1, 0))
            if abs(dy) > 0.1 or dx < 0:  # vertical / rotated text is not reflowed
                rotated += 1
                continue
            for span in line.get("spans", []):
                run = span_to_run(span)
                if not run.text:
                    continue
                bbox = tuple(span["bbox"])
                run.link = link_for(bbox, links)
                spans.append(RawSpan(run=run, bbox=bbox, baseline=span["origin"][1], line_id=line_id))
    return spans, rotated


def _image_from_block(block: dict) -> tuple[bytes, str] | None:
    """Return PNG/JPEG bytes usable by Word, converting other formats."""
    data = block.get("image")
    if not data:
        return None
    ext = (block.get("ext") or "").lower()
    try:
        if ext in ("jpeg", "jpg") and block.get("colorspace", 3) in (1, 3):
            return data, "jpeg"
        if ext == "png":
            return data, "png"
        pix = pymupdf.Pixmap(data)
        if pix.colorspace and pix.colorspace.n >= 4:  # CMYK etc.
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        return pix.tobytes("png"), "png"
    except Exception:
        log.warning("skipped an image that could not be decoded")
        return None


def _downscale(data: bytes, ext: str, bbox: BBox) -> tuple[bytes, str]:
    """Shrink very large images to ~300 DPI at their displayed size."""
    try:
        pix = pymupdf.Pixmap(data)
    except Exception:
        return data, ext
    target_w = (bbox[2] - bbox[0]) / 72 * MAX_IMAGE_DPI
    factor = 0
    while pix.width / (2 ** (factor + 1)) >= target_w and factor < 5:
        factor += 1
    if factor == 0:
        return data, ext
    pix.shrink(factor)
    if pix.colorspace and pix.colorspace.n >= 4:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    return pix.tobytes("png"), "png"


def _collect_images(textdict: dict, page_rect: pymupdf.Rect) -> list[Image]:
    images: list[Image] = []
    for block in textdict.get("blocks", []):
        if block.get("type") != 1:
            continue
        bbox = _clip(tuple(block["bbox"]), page_rect)
        if bbox[2] - bbox[0] < MIN_IMAGE_PT or bbox[3] - bbox[1] < MIN_IMAGE_PT:
            continue
        converted = _image_from_block(block)
        if not converted:
            continue
        data, ext = _downscale(*converted, bbox)
        images.append(Image(data=data, ext=ext, bbox=bbox))
    return images


def extract_page(page: pymupdf.Page, *, ocr_language: str, ocr_dpi: int, allow_ocr: bool) -> RawPage:
    rect = page.rect
    raw = RawPage(number=page.number + 1, width=rect.width, height=rect.height)
    links = _page_links(page)
    # Expand ligatures (ﬁ -> fi) so text stays searchable and spell-checkable in Word.
    flags = (pymupdf.TEXTFLAGS_DICT | pymupdf.TEXT_PRESERVE_IMAGES) & ~pymupdf.TEXT_PRESERVE_LIGATURES
    textdict = page.get_text("dict", flags=flags)

    spans, raw.skipped_rotated = _collect_spans(textdict, links)
    images = _collect_images(textdict, rect)
    page_area = _area(tuple(rect)) or 1.0
    coverage = sum(_area(im.bbox) for im in images) / page_area
    chars = sum(len(sp.run.text.strip()) for sp in spans)

    if chars < SCANNED_MAX_CHARS and coverage >= SCANNED_MIN_COVERAGE:
        raw.looks_scanned = True
        if allow_ocr:
            textpage = ocr.ocr_textpage(page, ocr_language, ocr_dpi)
            ocr_dict = page.get_text("dict", textpage=textpage)
            ocr_spans, _ = _collect_spans(ocr_dict, links)
            if sum(len(sp.run.text.strip()) for sp in ocr_spans) >= 3:
                spans = ocr_spans
                raw.ocr_used = True
                # The scan bitmap is replaced by its editable text.
                images = [im for im in images if _area(im.bbox) / page_area < SCANNED_MIN_COVERAGE]
            else:
                raw.ocr_found_nothing = True
    elif chars >= SCANNED_MAX_CHARS:
        # Full-page images behind real text are backgrounds or scans with a text layer.
        images = [im for im in images if _area(im.bbox) / page_area < 0.85]

    used: set[int] = set()
    if not raw.ocr_used:
        raw.tables, used = detect_tables(page, spans)
    raw.lines = spans_to_lines([sp for i, sp in enumerate(spans) if i not in used])
    raw.images = images
    return raw
