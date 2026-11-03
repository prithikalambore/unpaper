"""Layout analysis: turn extracted page content into an ordered, structured document.

Stages:
  1. Detect running headers/footers (text repeated near the top/bottom of pages).
  2. Determine reading order per page, including simple two-column regions.
  3. Group lines into paragraphs and classify headings and list items.
  4. Derive alignment, indentation and spacing (used fully in "layout" mode).

Everything here is deterministic geometry/typography heuristics - no ML or LLM.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from .extract import RawPage
from .model import (
    BBox,
    DocumentLayout,
    Element,
    HeaderFooterLine,
    Image,
    Line,
    PageLayout,
    Paragraph,
    Run,
    Table,
)
from .spans import merge_runs

Mode = Literal["layout", "clean"]

BULLET_RE = re.compile(r"^\s*([•◦▪▫●○■□►▸‣⁃∙"
                       r"·❖➢✓✔★☆◆◇→]|[-–—*](?=\s))\s*")
NUMBER_RE = re.compile(r"^\s*(\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|\(?(?:[ivx]{1,5}|[IVX]{1,5})[.)])\s+(?=\S)")

HEADER_ZONE = 0.11  # top/bottom share of the page searched for running headers/footers


# --------------------------------------------------------------------------- helpers


def _norm_key(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text.strip().lower()))


def _weighted_mode(pairs: list[tuple[object, int]], default):
    counter: Counter = Counter()
    for value, weight in pairs:
        counter[value] += weight
    return counter.most_common(1)[0][0] if counter else default


def _copy_runs(runs: list[Run]) -> list[Run]:
    return [Run(**r.__dict__) for r in runs]


# --------------------------------------------------------------------------- headers / footers


def _running_lines(pages: list[RawPage]) -> tuple[dict[int, list[Line]], dict[int, list[Line]]]:
    """Remove repeated header/footer lines from pages; return them per page number."""
    headers: dict[int, list[Line]] = {}
    footers: dict[int, list[Line]] = {}
    if len(pages) < 2:
        return headers, footers

    def zone(page: RawPage, line: Line) -> str | None:
        if line.bbox[3] <= page.height * HEADER_ZONE:
            return "header"
        if line.bbox[1] >= page.height * (1 - HEADER_ZONE):
            return "footer"
        return None

    counts: Counter = Counter()
    for page in pages:
        keys = {(zone(page, ln), _norm_key(ln.text)) for ln in page.lines if zone(page, ln)}
        counts.update(keys)
    needed = max(2, math.ceil(len(pages) * 0.5))
    running = {key for key, n in counts.items() if n >= needed and key[1]}

    for page in pages:
        keep = []
        for ln in page.lines:
            z = zone(page, ln)
            key = (z, _norm_key(ln.text))
            if z and key in running:
                (headers if z == "header" else footers).setdefault(page.number, []).append(ln)
            else:
                keep.append(ln)
        page.lines = keep
    return headers, footers


def _with_fields(runs: list[Run], page_no: int, total: int) -> list[Run]:
    """Replace the literal page number (and page count) with Word fields."""
    out: list[Run] = []
    for run in runs:
        parts = re.split(r"(\d+)", run.text)
        for part in parts:
            if not part:
                continue
            piece = Run(**run.__dict__)
            piece.text = part
            if part.isdigit() and int(part) == page_no:
                piece.field = "PAGE"
            elif part.isdigit() and int(part) == total and total != page_no:
                piece.field = "NUMPAGES"
            out.append(piece)
    return out


def _header_footer(lines_by_page: dict[int, list[Line]], width: float, margins, total: int) -> list[HeaderFooterLine]:
    if not lines_by_page:
        return []
    page_no = min(lines_by_page)
    result = []
    lines = sorted(lines_by_page[page_no], key=lambda ln: (ln.baseline, ln.bbox[0]))
    content_left, content_right = margins[0], width - margins[2]
    for line in lines:
        left_gap = line.bbox[0] - content_left
        right_gap = content_right - line.bbox[2]
        if abs(left_gap - right_gap) < 0.08 * (content_right - content_left) and left_gap > 20:
            align = "center"
        elif right_gap < 20 and left_gap > 0.3 * (content_right - content_left):
            align = "right"
        else:
            align = "left"
        runs = _with_fields(_copy_runs(line.runs), page_no, total)
        result.append(HeaderFooterLine(text=line.text, runs=runs, align=align))
    return result


# --------------------------------------------------------------------------- reading order


@dataclass
class _Item:
    bbox: BBox
    obj: Line | Table | Image
    col: tuple[float, float] = (0.0, 0.0)

    @property
    def key_y(self) -> float:
        return self.obj.baseline if isinstance(self.obj, Line) else self.bbox[1]


def _order(items: list[_Item], left: float, right: float) -> list[_Item]:
    """Order items top-to-bottom, reading detected two-column bands column by column."""
    width = max(right - left, 1.0)
    mid = (left + right) / 2
    slack = 0.02 * width

    def side(it: _Item) -> str:
        if it.bbox[2] <= mid + slack:
            return "L"
        if it.bbox[0] >= mid - slack:
            return "R"
        return "F"

    items = sorted(items, key=lambda it: (it.key_y, it.bbox[0]))
    ordered: list[_Item] = []
    band: list[_Item] = []

    def flush() -> None:
        if not band:
            return
        lefts = [it for it in band if side(it) == "L"]
        rights = [it for it in band if side(it) == "R"]
        half = width / 2

        def wide(group: list[_Item]) -> bool:
            widths = [it.bbox[2] - it.bbox[0] for it in group if isinstance(it.obj, Line)]
            return bool(widths) and statistics.median(widths) >= 0.6 * half

        if len(lefts) >= 3 and len(rights) >= 3 and wide(lefts) and wide(rights):
            l_col = (min(it.bbox[0] for it in lefts), max(it.bbox[2] for it in lefts))
            r_col = (min(it.bbox[0] for it in rights), max(it.bbox[2] for it in rights))
            for it in lefts:
                it.col = l_col
            for it in rights:
                it.col = r_col
            ordered.extend(lefts + rights)
        else:
            for it in band:
                it.col = (left, right)
            ordered.extend(band)
        band.clear()

    for it in items:
        if side(it) == "F":
            flush()
            it.col = (left, right)
            ordered.append(it)
        else:
            band.append(it)
    flush()
    return ordered


# --------------------------------------------------------------------------- paragraphs


@dataclass
class _Para:
    lines: list[Line]
    col: tuple[float, float]
    is_list: bool = False


def _starts_list(text: str) -> bool:
    return bool(BULLET_RE.match(text) or NUMBER_RE.match(text))


def _join_line(runs: list[Run], line: Line, separator: str) -> list[Run]:
    new = _copy_runs(line.runs)
    if runs:
        last = runs[-1]
        stripped = last.text.rstrip()
        if separator == " " and stripped.endswith(("-", "­")) and len(stripped) > 1 \
                and stripped[-2].isalpha() and new[0].text[:1].islower():
            last.text = stripped[:-1]  # de-hyphenate a word broken across lines
        elif not last.text.endswith((" ", "\t")):
            new[0].text = separator + new[0].text.lstrip()
    return merge_runs(runs + new)


def _split_paragraphs(items: list[_Item], pitch_ratio: float) -> list[_Para | _Item]:
    out: list[_Para | _Item] = []
    current: _Para | None = None
    threshold = pitch_ratio * 1.2 + 0.15

    for it in items:
        if not isinstance(it.obj, Line):
            current = None
            out.append(it)
            continue
        line = it.obj
        if current is not None:
            prev = current.lines[-1]
            size = max(prev.size, line.size)
            same_baseline = abs(line.baseline - prev.baseline) < 0.35 * size and line.bbox[0] > prev.bbox[2] - 1
            if same_baseline:
                current.lines[-1] = Line(
                    bbox=(prev.bbox[0], min(prev.bbox[1], line.bbox[1]), line.bbox[2], max(prev.bbox[3], line.bbox[3])),
                    runs=_join_line(_copy_runs(prev.runs), line,
                                    "\t" if line.bbox[0] - prev.bbox[2] > 1.5 * size else " "),
                    baseline=prev.baseline,
                )
                continue
            if not _breaks(current, prev, line, threshold):
                current.lines.append(line)
                continue
        current = _Para(lines=[line], col=it.col, is_list=_starts_list(line.text))
        out.append(current)
    return out


def _breaks(para: _Para, prev: Line, line: Line, threshold: float) -> bool:
    size = max(min(prev.size, line.size), 1.0)
    pitch = line.baseline - prev.baseline
    if pitch <= 0 or pitch / size > threshold:
        return True
    if line.bbox[1] - prev.bbox[3] > 0.8 * size:
        return True
    if abs(prev.size - line.size) > 1.0 or prev.all_bold != line.all_bold:
        return True
    if _starts_list(line.text):
        return True
    if line.bbox[0] > prev.bbox[2] or line.bbox[2] < prev.bbox[0]:
        return True  # no horizontal overlap: different block
    first = para.lines[0]
    if para.is_list:
        if line.bbox[0] > first.bbox[0] + 0.5 * size:
            return False  # aligned under the item text: hanging-indent continuation
        # Back at the marker's x: still the same item if the previous line ran to the margin.
        col_width = para.col[1] - para.col[0]
        return prev.bbox[2] < para.col[1] - 0.12 * col_width
    if line.bbox[0] > prev.bbox[0] + 0.8 * size:
        return True  # indented line starts a new paragraph
    if len(para.lines) >= 2 and line.bbox[0] < prev.bbox[0] - 0.8 * size:
        return True
    col_width = para.col[1] - para.col[0]
    right = max(ln.bbox[2] for ln in para.lines) if len(para.lines) >= 2 else para.col[1]
    if prev.text.rstrip().endswith((".", "!", "?", ":", "”", '"')) and prev.bbox[2] < right - 0.15 * col_width:
        return True  # a short line ending a sentence closes the paragraph
    return False


# --------------------------------------------------------------------------- classification


def _para_size(lines: list[Line]) -> float:
    return _weighted_mode([(ln.size, len(ln.text)) for ln in lines], lines[0].size)


def _alignment(p: _Para) -> tuple[str, float, float]:
    """Return (align, left_indent, first_line_indent) in points relative to the column."""
    col_left, col_right = p.col
    width = max(col_right - col_left, 1.0)
    lines = p.lines
    tol = max(6.0, 0.06 * width)

    def centered(ln: Line) -> bool:
        lg, rg = ln.bbox[0] - col_left, col_right - ln.bbox[2]
        return abs(lg - rg) <= tol and lg > 12

    if all(centered(ln) for ln in lines):
        return "center", 0.0, 0.0
    if len(lines) == 1:
        ln = lines[0]
        lg, rg = ln.bbox[0] - col_left, col_right - ln.bbox[2]
        if rg < 8 and lg > 0.3 * width:
            return "right", 0.0, 0.0
        return "left", max(0.0, lg), 0.0
    body = lines[1:]
    left = min(ln.bbox[0] for ln in body)
    indent = max(0.0, left - col_left)
    first = lines[0].bbox[0] - left
    align = "left"
    rights = [ln.bbox[2] for ln in lines[:-1]]
    if len(rights) >= 2 and max(rights) - min(rights) <= 2.5 and col_right - max(rights) < 12:
        align = "justify"
    return align, indent, first


WORD_SINGLE_LINE = 1.17  # Word's "single" line height as a multiple of the font size (approx.)


def _spacing_multiple(pitch_ratio: float) -> float:
    return round(min(max(pitch_ratio / WORD_SINGLE_LINE, 0.8), 3.0), 2)


def _line_spacing(lines: list[Line], size: float, doc_spacing: float) -> float | None:
    """Paragraph line spacing, only when it differs from the document default."""
    if len(lines) < 2:
        return None
    pitches = [b.baseline - a.baseline for a, b in zip(lines, lines[1:]) if b.baseline > a.baseline]
    if not pitches:
        return None
    multiple = _spacing_multiple(statistics.median(pitches) / max(size, 1.0))
    if abs(multiple - doc_spacing) <= 0.08 * doc_spacing:
        return None
    return multiple


def _to_paragraph(p: _Para, mode: Mode, body_size: float, list_base: float, doc_spacing: float) -> Paragraph:
    runs: list[Run] = []
    for i, line in enumerate(p.lines):
        runs = _join_line(runs, line, " ") if i else _copy_runs(line.runs)
    if runs:
        runs[0].text = runs[0].text.lstrip()
        runs[-1].text = runs[-1].text.rstrip()
    runs = [r for r in runs if r.text or r.field]
    x0 = min(ln.bbox[0] for ln in p.lines)
    bbox = (x0, min(ln.bbox[1] for ln in p.lines), max(ln.bbox[2] for ln in p.lines),
            max(ln.bbox[3] for ln in p.lines))
    para = Paragraph(runs=runs, bbox=bbox)
    size = _para_size(p.lines)
    text = para.text

    align, indent, first = _alignment(p)
    para.align = align
    para.left_indent, para.first_line_indent = indent, first
    para.line_spacing = _line_spacing(p.lines, size, doc_spacing)

    is_heading_size = size >= body_size + 1.5 and len(text) <= 250 and len(p.lines) <= 4
    is_bold_heading = (all(ln.all_bold for ln in p.lines) and len(p.lines) == 1 and len(text) <= 100
                       and size >= body_size - 0.5 and not text.rstrip().endswith((".", ",", ";")))
    if (is_heading_size or is_bold_heading) and not BULLET_RE.match(text):
        para.kind = "heading"
        para.heading_level = 0  # assigned later from the document's size hierarchy
    elif p.is_list:
        para.kind = "list"
        m = BULLET_RE.match(text)
        if m:
            para.list_type = "bullet"
            _strip_prefix(para.runs, m.end())
        else:
            m = NUMBER_RE.match(text)
            para.list_type = "number"
            # Hanging indent only when continuation lines really are indented under the text.
            hanging = len(p.lines) > 1 and p.lines[1].bbox[0] > p.lines[0].bbox[0] + 0.5 * size
            _replace_after_prefix(para.runs, m.group(1), "\t" if hanging else " ")
            para.list_hanging = hanging
        para.list_level = max(0, min(2, int((p.lines[0].bbox[0] - list_base) / 14)))

    if mode == "clean":
        if para.kind != "heading" and not (para.align == "center" and len(p.lines) == 1):
            para.align = "left"
        if para.align == "justify":
            para.align = "left"
        para.left_indent = para.first_line_indent = 0.0
        para.line_spacing = None
    return para


def _strip_prefix(runs: list[Run], n: int) -> None:
    while n > 0 and runs:
        take = min(n, len(runs[0].text))
        runs[0].text = runs[0].text[take:]
        n -= take
        if not runs[0].text:
            runs.pop(0)


def _replace_after_prefix(runs: list[Run], marker: str, separator: str) -> None:
    """Normalise "1.   Item" to "1.<separator>Item" (a tab aligns it on a hanging indent)."""
    full = "".join(r.text for r in runs)
    m = re.match(r"\s*" + re.escape(marker) + r"\s+", full)
    if not m:
        return
    _strip_prefix(runs, m.end())
    head = Run(**(runs[0].__dict__ if runs else Run(text="").__dict__))
    head.text, head.link = marker + separator, None
    runs.insert(0, head)


# --------------------------------------------------------------------------- public API


def analyse(pages: list[RawPage], mode: Mode, title: str = "") -> DocumentLayout:
    headers, footers = _running_lines(pages)

    all_lines = [ln for p in pages for ln in p.lines]
    body_size = _weighted_mode([(ln.size, len(ln.text)) for ln in all_lines], 11.0)
    body_font = _weighted_mode([(r.font, len(r.text)) for ln in all_lines for r in ln.runs if r.font], "")

    ratios = []
    for p in pages:
        ls = sorted(p.lines, key=lambda ln: ln.baseline)
        for a, b in zip(ls, ls[1:]):
            if abs(a.size - b.size) < 0.5 and a.size > 0:
                r = (b.baseline - a.baseline) / a.size
                if 1.0 <= r <= 2.5:
                    ratios.append(round(r, 1))
    pitch_ratio = Counter(ratios).most_common(1)[0][0] if ratios else 1.2
    # Typical whitespace between two lines of one paragraph; Word reproduces it via line spacing,
    # so only the extra whitespace above a paragraph becomes "space before".
    line_gaps = []
    for p in pages:
        ls = sorted(p.lines, key=lambda ln: ln.baseline)
        for a, b in zip(ls, ls[1:]):
            if a.size > 0 and abs((b.baseline - a.baseline) / a.size - pitch_ratio) <= 0.1:
                line_gaps.append(b.bbox[1] - a.bbox[3])
    line_gap = max(0.0, statistics.median(line_gaps)) if line_gaps else 0.2 * body_size
    multiple = _spacing_multiple(pitch_ratio)
    doc_spacing = multiple if mode == "layout" and not 0.92 <= multiple <= 1.08 else 1.0

    # Margins from the extent of body content over all pages.
    boxes = [ln.bbox for p in pages for ln in p.lines] + \
            [t.bbox for p in pages for t in p.tables] + [im.bbox for p in pages for im in p.images]
    ref_w = Counter(round(p.width) for p in pages).most_common(1)[0][0] if pages else 595
    ref_h = Counter(round(p.height) for p in pages).most_common(1)[0][0] if pages else 842

    def clamp(v: float) -> float:
        return float(min(max(v, 18.0), 108.0))

    if boxes:
        margins = (
            clamp(min(b[0] for b in boxes)),
            clamp(min(b[1] for b in boxes) - 4),
            clamp(ref_w - max(b[2] for b in boxes)),
            clamp(ref_h - max(b[3] for b in boxes) - 4),
        )
    else:
        margins = (72.0, 72.0, 72.0, 72.0)

    total_pages = len(pages)
    doc = DocumentLayout(
        pages=[], body_size=body_size, body_font=body_font, margins=margins, title=title,
        line_spacing=doc_spacing if doc_spacing != 1.0 else None,
        header=_header_footer(headers, ref_w, margins, total_pages),
        footer=_header_footer(footers, ref_w, margins, total_pages),
    )

    for raw in pages:
        page = PageLayout(number=raw.number, width=raw.width, height=raw.height, ocr_used=raw.ocr_used)
        left = margins[0]
        right = raw.width - margins[2]
        items = [_Item(bbox=ln.bbox, obj=ln) for ln in raw.lines]
        items += [_Item(bbox=t.bbox, obj=t) for t in raw.tables]
        items += [_Item(bbox=im.bbox, obj=im) for im in raw.images]
        ordered = _order(items, left, right)
        chunks = _split_paragraphs(ordered, pitch_ratio)

        list_x = [c.lines[0].bbox[0] for c in chunks if isinstance(c, _Para) and c.is_list]
        list_base = min(list_x) if list_x else left

        prev_bottom = margins[1]
        for chunk in chunks:
            if isinstance(chunk, _Para):
                el: Element = _to_paragraph(chunk, mode, body_size, list_base, doc_spacing)
                if not el.text.strip():
                    continue
                top, bottom = el.bbox[1], el.bbox[3]
            else:
                el = chunk.obj
                top, bottom = chunk.bbox[1], chunk.bbox[3]
                if isinstance(el, Image):
                    lg = chunk.bbox[0] - chunk.col[0]
                    rg = chunk.col[1] - chunk.bbox[2]
                    if abs(lg - rg) < 0.08 * (chunk.col[1] - chunk.col[0]) and lg > 12:
                        el.align = "center"
                    elif rg < 8 and lg > 20:
                        el.align = "right"
            if mode == "layout":
                gap = top - prev_bottom - (line_gap if page.elements else 0.0)
                el.space_before = round(min(max(gap, 0.0), 60.0 if page.elements else 200.0), 1)
            prev_bottom = bottom
            page.elements.append(el)

        if mode == "layout" and page.elements and doc.pages:
            page.elements[0].page_break_before = True
        doc.pages.append(page)

    _join_across_pages(doc)
    _assign_heading_levels(doc, body_size)
    return doc


def _join_across_pages(doc: DocumentLayout) -> None:
    """Re-join a sentence that the PDF split across a page boundary."""
    for prev_page, page in zip(doc.pages, doc.pages[1:]):
        if not prev_page.elements or not page.elements:
            continue
        last, first = prev_page.elements[-1], page.elements[0]
        if not (isinstance(last, Paragraph) and isinstance(first, Paragraph)):
            continue
        if last.kind == "heading" or first.kind != "body" or not first.text[:1].islower():
            continue
        if last.text.rstrip().endswith((".", "!", "?", ":", ";")):
            continue
        tail = last.runs[-1]
        if tail.text.endswith("-") and len(tail.text) > 1 and tail.text[-2].isalpha():
            tail.text = tail.text[:-1]
        elif not tail.text.endswith(" "):
            tail.text += " "
        last.runs = merge_runs(last.runs + first.runs)
        page.elements.pop(0)
        # The continuation no longer starts the page, so don't force a break before what follows.
        if page.elements:
            page.elements[0].page_break_before = False


def _assign_heading_levels(doc: DocumentLayout, body_size: float) -> None:
    size_of: dict[int, float] = {}
    sizes: set[float] = set()
    for page in doc.pages:
        for el in page.elements:
            if isinstance(el, Paragraph) and el.kind == "heading":
                s = _weighted_mode([(r.size, len(r.text)) for r in el.runs], body_size)
                size_of[id(el)] = s
                if s >= body_size + 1.5:
                    sizes.add(s)
    ranked = sorted(sizes, reverse=True)
    for page in doc.pages:
        for el in page.elements:
            if isinstance(el, Paragraph) and el.kind == "heading":
                s = size_of[id(el)]
                level = ranked.index(s) + 1 if s in ranked else len(ranked) + 1
                el.heading_level = min(level, 6)
