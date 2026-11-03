"""Helpers for turning PyMuPDF spans into normalised runs and visual lines."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .fonts import is_bold_name, is_italic_name, word_font_name
from .model import BBox, Line, Run

# Characters that are illegal in XML 1.0 (python-docx would refuse them).
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

# Bullet glyphs from Symbol / Wingdings fonts land in the Private Use Area.
_PUA_BULLETS = {
    "": "•", "": "▪", "": "❖", "": "➢",
    "": "✓", "": "◦", "": "■", "": "➢",
}

SAFE_LINK_SCHEMES = ("http://", "https://", "mailto:")

FLAG_SUPERSCRIPT = 1
FLAG_ITALIC = 2
FLAG_BOLD = 16


def clean_text(text: str) -> str:
    for src, dst in _PUA_BULLETS.items():
        text = text.replace(src, dst)
    return _XML_ILLEGAL.sub("", text)


@dataclass
class RawSpan:
    run: Run
    bbox: BBox
    baseline: float
    line_id: int

    @property
    def center(self) -> tuple[float, float]:
        return ((self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2)


def span_to_run(span: dict) -> Run:
    flags = span.get("flags", 0)
    font = span.get("font", "")
    return Run(
        text=clean_text(span.get("text", "")),
        font=word_font_name(font),
        size=round(float(span.get("size", 11.0)) * 2) / 2 or 11.0,
        bold=bool(flags & FLAG_BOLD) or is_bold_name(font),
        italic=bool(flags & FLAG_ITALIC) or is_italic_name(font),
        color=int(span.get("color", 0)) & 0xFFFFFF,
        superscript=bool(flags & FLAG_SUPERSCRIPT),
    )


def link_for(bbox: BBox, links: list[tuple[BBox, str]]) -> str | None:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    for rect, uri in links:
        if rect[0] - 1 <= cx <= rect[2] + 1 and rect[1] - 1 <= cy <= rect[3] + 1:
            return uri
    return None


def merge_runs(runs: list[Run]) -> list[Run]:
    merged: list[Run] = []
    for run in runs:
        if not run.text:
            continue
        if merged and merged[-1].same_format(run):
            merged[-1].text += run.text
        else:
            merged.append(Run(**run.__dict__))
    return merged


def _join(left: list[Run], right: list[Run], gap: float, size: float, sep: str = " ") -> list[Run]:
    """Concatenate run lists, inserting a separator when the visual gap needs one."""
    if left and right and gap > 0.15 * size:
        if not left[-1].text.endswith((" ", "\t")) and not right[0].text.startswith((" ", "\t")):
            filler = Run(**left[-1].__dict__)
            filler.text = sep
            filler.link = None
            left = left + [filler]
    return left + right


def spans_to_lines(spans: list[RawSpan]) -> list[Line]:
    """Group spans by their PDF line, then merge fragments sharing a baseline.

    PDF producers often emit one visual line as several text objects (for
    example a bullet glyph and the item text). Fragments are merged only when
    the horizontal gap is small, so neighbouring columns stay separate.
    """
    by_line: dict[int, list[RawSpan]] = {}
    for sp in spans:
        by_line.setdefault(sp.line_id, []).append(sp)

    lines: list[Line] = []
    for group in by_line.values():
        group.sort(key=lambda s: s.bbox[0])
        runs: list[Run] = []
        bbox = group[0].bbox
        prev_x1 = None
        for sp in group:
            if prev_x1 is not None:
                runs = _join(runs, [sp.run], sp.bbox[0] - prev_x1, sp.run.size)
            else:
                runs = [sp.run]
            prev_x1 = sp.bbox[2]
            bbox = (min(bbox[0], sp.bbox[0]), min(bbox[1], sp.bbox[1]),
                    max(bbox[2], sp.bbox[2]), max(bbox[3], sp.bbox[3]))
        runs = merge_runs(runs)
        if runs and "".join(r.text for r in runs).strip():
            lines.append(Line(bbox=bbox, runs=runs, baseline=group[0].baseline))

    return merge_same_baseline(lines, max_gap_factor=1.0)


def baseline_clusters(lines: list[Line]) -> list[list[Line]]:
    """Group lines whose baselines (nearly) coincide; each group is sorted left to right."""
    clusters: list[list[Line]] = []
    for line in sorted(lines, key=lambda ln: ln.baseline):
        if clusters:
            anchor = clusters[-1][0]
            if abs(anchor.baseline - line.baseline) < 0.35 * max(anchor.size, line.size):
                clusters[-1].append(line)
                continue
        clusters.append([line])
    for cluster in clusters:
        cluster.sort(key=lambda ln: ln.bbox[0])
    return clusters


def merge_same_baseline(lines: list[Line], max_gap_factor: float, sep: str = " ") -> list[Line]:
    out: list[Line] = []
    for cluster in baseline_clusters(lines):
        current = cluster[0]
        for line in cluster[1:]:
            size = max(current.size, line.size)
            gap = line.bbox[0] - current.bbox[2]
            if -1 <= gap < max_gap_factor * size:
                current.runs = merge_runs(_join(current.runs, line.runs, gap, size, sep))
                current.bbox = (current.bbox[0], min(current.bbox[1], line.bbox[1]),
                                max(current.bbox[2], line.bbox[2]), max(current.bbox[3], line.bbox[3]))
            else:
                out.append(current)
                current = line
        out.append(current)
    return out
