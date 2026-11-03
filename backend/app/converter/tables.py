"""Detect ruled tables and rebuild them as editable table structures.

Detection uses PyMuPDF's ``find_tables`` (ruling-line strategy). Cell text is
rebuilt from the page's spans rather than the plain-text ``extract()`` output
so bold/italic/size formatting survives inside cells. Merged cells are
recovered geometrically from cell bounding boxes.
"""

from __future__ import annotations

import logging

import pymupdf

from .model import Run, Table, TableCell
from .spans import RawSpan, merge_runs, spans_to_lines

log = logging.getLogger(__name__)


def _cluster(values: list[float], tol: float = 2.0) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _nearest(edges: list[float], value: float) -> int:
    return min(range(len(edges)), key=lambda i: abs(edges[i] - value))


def _index_for(edges: list[float], value: float) -> int:
    for i in range(len(edges) - 1):
        if edges[i] <= value < edges[i + 1]:
            return i
    return len(edges) - 2 if value >= edges[-1] else 0


def _cell_runs(spans: list[RawSpan]) -> list[list[Run]]:
    """Join a cell's wrapped lines into one paragraph (new paragraph on big gaps)."""
    lines = spans_to_lines(spans)
    lines.sort(key=lambda ln: (ln.baseline, ln.bbox[0]))
    paragraphs: list[list[Run]] = []
    prev = None
    for line in lines:
        runs = [Run(**r.__dict__) for r in line.runs]
        if prev is not None and line.bbox[1] - prev.bbox[3] < 0.6 * line.size:
            last = paragraphs[-1]
            if last and last[-1].text.endswith("-") and runs[0].text[:1].islower():
                last[-1].text = last[-1].text[:-1]
            elif last and not last[-1].text.endswith(" "):
                runs[0].text = " " + runs[0].text.lstrip()
            paragraphs[-1] = merge_runs(last + runs)
        else:
            paragraphs.append(runs)
        prev = line
    return paragraphs


def detect_tables(page: pymupdf.Page, spans: list[RawSpan]) -> tuple[list[Table], set[int]]:
    """Return tables found on the page plus the indices of spans they consumed."""
    try:
        # use_layout=False: plain ruling-line detection. PyMuPDF 2's default gates
        # results through an ML layout model, which is slower and mis-reads merged cells.
        finder = page.find_tables(use_layout=False)
    except Exception:  # malformed drawings can make detection fail; fall back to text
        log.warning("table detection failed on a page; continuing without tables")
        return [], set()

    tables: list[Table] = []
    used: set[int] = set()
    for tb in finder.tables:
        n_rows, n_cols = tb.row_count, tb.col_count
        if n_rows < 2 or n_cols < 2:
            continue
        bbox = tuple(tb.bbox)

        cells = [c for row in tb.rows for c in row.cells if c is not None]
        x_edges = _cluster([c[0] for c in cells] + [c[2] for c in cells])
        if len(x_edges) != n_cols + 1:
            step = (bbox[2] - bbox[0]) / n_cols
            x_edges = [bbox[0] + i * step for i in range(n_cols + 1)]
        y_edges = [row.bbox[1] for row in tb.rows] + [tb.rows[-1].bbox[3]]

        grid: list[list[TableCell | None]] = [[None] * n_cols for _ in range(n_rows)]
        owner: list[list[tuple[int, int] | None]] = [[None] * n_cols for _ in range(n_rows)]
        for r, row in enumerate(tb.rows):
            for c, cell in enumerate(row.cells):
                if cell is None or owner[r][c] is not None:
                    continue
                c_end = max(c + 1, _nearest(x_edges, cell[2]))
                r_end = max(r + 1, _nearest(y_edges, cell[3]))
                c_end, r_end = min(c_end, n_cols), min(r_end, n_rows)
                # Never let a merge swallow a cell that is already owned.
                if any(owner[rr][cc] is not None for rr in range(r, r_end) for cc in range(c, c_end)):
                    c_end, r_end = c + 1, r + 1
                grid[r][c] = TableCell(paragraphs=[], row_span=r_end - r, col_span=c_end - c)
                for rr in range(r, r_end):
                    for cc in range(c, c_end):
                        owner[rr][cc] = (r, c)
        # Any position PyMuPDF left uncovered becomes an empty cell of its own.
        for r in range(n_rows):
            for c in range(n_cols):
                if owner[r][c] is None:
                    grid[r][c] = TableCell(paragraphs=[])
                    owner[r][c] = (r, c)

        cell_spans: dict[tuple[int, int], list[RawSpan]] = {}
        table_used: set[int] = set()
        for i, sp in enumerate(spans):
            cx, cy = sp.center
            if i in used or not (bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]):
                continue
            table_used.add(i)
            key = owner[_index_for(y_edges, cy)][_index_for(x_edges, cx)]
            cell_spans.setdefault(key, []).append(sp)

        if not any(sp.run.text.strip() for group in cell_spans.values() for sp in group):
            continue  # an empty grid is decoration, not a table
        used |= table_used

        for (r, c), group in cell_spans.items():
            grid[r][c].paragraphs = _cell_runs(group)

        widths = [x_edges[i + 1] - x_edges[i] for i in range(n_cols)]
        tables.append(Table(rows=grid, col_widths=widths, bbox=bbox))
    return tables, used
