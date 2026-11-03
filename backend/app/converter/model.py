"""Intermediate document model shared by extraction, layout analysis and DOCX output.

The converter never writes Word XML directly from PDF primitives. PDF content
is first normalised into these plain dataclasses, which keeps each stage
independently testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

BBox = tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points


def union(a: BBox, b: BBox) -> BBox:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


@dataclass
class Run:
    """A run of text sharing one set of character formatting."""

    text: str
    font: str = ""
    size: float = 11.0
    bold: bool = False
    italic: bool = False
    color: int = 0  # 0xRRGGBB
    superscript: bool = False
    link: str | None = None
    field: str | None = None  # "PAGE" / "NUMPAGES": rendered as a Word field instead of text

    def same_format(self, other: Run) -> bool:
        return (
            self.field is None
            and other.field is None
            and self.font == other.font
            and abs(self.size - other.size) < 0.25
            and self.bold == other.bold
            and self.italic == other.italic
            and self.color == other.color
            and self.superscript == other.superscript
            and self.link == other.link
        )


@dataclass
class Line:
    """One visual line of text (may combine several PDF 'lines' on one baseline)."""

    bbox: BBox
    runs: list[Run]
    baseline: float

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def size(self) -> float:
        """Dominant font size, weighted by character count."""
        weights: dict[float, int] = {}
        for r in self.runs:
            if r.text.strip():
                key = round(r.size * 2) / 2
                weights[key] = weights.get(key, 0) + len(r.text.strip())
        if not weights:
            return self.runs[0].size if self.runs else 11.0
        return max(weights.items(), key=lambda kv: kv[1])[0]

    @property
    def all_bold(self) -> bool:
        visible = [r for r in self.runs if r.text.strip()]
        return bool(visible) and all(r.bold for r in visible)


@dataclass
class Paragraph:
    runs: list[Run]
    bbox: BBox
    kind: Literal["body", "heading", "list"] = "body"
    heading_level: int = 0
    list_type: Literal["bullet", "number", ""] = ""
    list_level: int = 0
    list_hanging: bool = False  # numbered item whose wrapped lines align under the text
    align: Literal["left", "center", "right", "justify"] = "left"
    left_indent: float = 0.0  # points
    first_line_indent: float = 0.0  # points (negative = hanging)
    space_before: float = 0.0  # points
    line_spacing: float | None = None  # multiple of single spacing
    page_break_before: bool = False

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


@dataclass
class TableCell:
    paragraphs: list[list[Run]]  # one list of runs per line in the cell
    row_span: int = 1
    col_span: int = 1


@dataclass
class Table:
    # rows[r][c] is a TableCell for an origin cell or None for a cell covered by a merge
    rows: list[list[TableCell | None]]
    col_widths: list[float]  # points
    bbox: BBox
    space_before: float = 0.0
    page_break_before: bool = False


@dataclass
class Image:
    data: bytes  # PNG or JPEG
    ext: Literal["png", "jpeg"]
    bbox: BBox
    align: Literal["left", "center", "right"] = "left"
    space_before: float = 0.0
    page_break_before: bool = False


Element = Paragraph | Table | Image


@dataclass
class HeaderFooterLine:
    text: str
    runs: list[Run]
    align: Literal["left", "center", "right"]


@dataclass
class PageLayout:
    number: int  # 1-based
    width: float
    height: float
    elements: list[Element] = field(default_factory=list)
    ocr_used: bool = False


@dataclass
class DocumentLayout:
    pages: list[PageLayout]
    body_size: float = 11.0
    body_font: str = ""
    margins: tuple[float, float, float, float] = (72, 72, 72, 72)  # left, top, right, bottom
    line_spacing: float | None = None  # document default line spacing multiple (layout mode)
    header: list[HeaderFooterLine] = field(default_factory=list)
    footer: list[HeaderFooterLine] = field(default_factory=list)
    title: str = ""
