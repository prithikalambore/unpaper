"""Write a DocumentLayout to a .docx file with python-docx.

Design choices for compatibility with Word (Windows/macOS), Apple Pages,
Google Docs and LibreOffice:
  * Flowing text only - no absolutely positioned text boxes or frames, which
    are hard to edit and render inconsistently (especially in Pages).
  * Built-in paragraph styles (Heading 1-6, List Bullet, Header/Footer) so the
    document structure is recognised by every editor.
  * Inline images only; standard complex fields for page numbers.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from .model import DocumentLayout, HeaderFooterLine, Image, Paragraph, Run, Table

log = logging.getLogger(__name__)

ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}
CLEAN_FONT = "Calibri"
CLEAN_SIZE = 11
LINK_COLOR = RGBColor(0x05, 0x63, 0xC1)


class DocxWriter:
    def __init__(self, layout: DocumentLayout, mode: str):
        self.layout = layout
        self.mode = mode
        self.doc = Document()
        self.images_written = 0
        self.images_skipped = 0
        self.tables_written = 0

    # ------------------------------------------------------------------ setup

    def _setup(self) -> None:
        doc, layout = self.doc, self.layout
        props = doc.core_properties
        # python-docx's template carries placeholder metadata; never leak it or user data.
        props.author = ""
        props.last_modified_by = ""
        props.comments = ""
        props.keywords = ""
        props.subject = ""
        props.category = ""
        props.title = (layout.title or "")[:255]
        now = datetime.now(timezone.utc).replace(microsecond=0)
        props.created = now
        props.modified = now
        props.revision = 1

        normal = doc.styles["Normal"]
        pf = normal.paragraph_format
        if self.mode == "layout":
            self._set_style_font(normal, layout.body_font or CLEAN_FONT)
            normal.font.size = Pt(layout.body_size or CLEAN_SIZE)
            pf.space_before = Pt(0)
            pf.space_after = Pt(0)
            if layout.line_spacing:
                pf.line_spacing = layout.line_spacing
        else:
            self._set_style_font(normal, CLEAN_FONT)
            normal.font.size = Pt(CLEAN_SIZE)
            pf.space_after = Pt(8)
            pf.line_spacing = 1.15
        for level in range(1, 7):
            style = doc.styles[f"Heading {level}"]
            style.font.color.rgb = RGBColor(0x1F, 0x1F, 0x1F)
            self._set_style_font(style, layout.body_font if self.mode == "layout" and layout.body_font else CLEAN_FONT)

        first = layout.pages[0] if layout.pages else None
        self._configure_section(doc.sections[0], first.width if first else 595, first.height if first else 842)

    def _configure_section(self, section, width: float, height: float) -> None:
        left, top, right, bottom = self.layout.margins
        if self.mode == "clean":
            left = right = top = bottom = 72.0
        section.orientation = WD_ORIENT.LANDSCAPE if width > height else WD_ORIENT.PORTRAIT
        section.page_width = Pt(width)
        section.page_height = Pt(height)
        section.left_margin, section.right_margin = Pt(left), Pt(right)
        section.top_margin, section.bottom_margin = Pt(top), Pt(bottom)
        section.header_distance = Pt(max(12.0, min(36.0, top / 2)))
        section.footer_distance = Pt(max(12.0, min(36.0, bottom / 2)))

    def _available_width(self) -> float:
        section = self.doc.sections[-1]
        return (section.page_width - section.left_margin - section.right_margin) / 12700  # EMU -> pt

    # ------------------------------------------------------------------ runs

    @staticmethod
    def _set_style_font(style, name: str) -> None:
        if not name:
            return
        style.font.name = name
        fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
        fonts.set(qn("w:eastAsia"), name)
        fonts.set(qn("w:cs"), name)
        for theme_attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            fonts.attrib.pop(qn(theme_attr), None)  # theme fonts would override the name

    def _style_run(self, run, r: Run, para_size: float, heading: bool) -> None:
        if self.mode == "layout":
            run.bold = r.bold
            run.italic = r.italic
            if r.font:
                run.font.name = r.font
                rfonts = run._element.get_or_add_rPr().get_or_add_rFonts()
                rfonts.set(qn("w:eastAsia"), r.font)
                rfonts.set(qn("w:cs"), r.font)
            run.font.size = Pt(para_size if r.superscript else r.size)
            run.font.color.rgb = RGBColor.from_string(f"{r.color:06X}")
        else:
            if not heading:
                run.bold = r.bold or None
            run.italic = r.italic or None
        if r.superscript:
            run.font.superscript = True
        if r.link:
            run.font.underline = True
            if self.mode == "clean" or r.color == 0:
                run.font.color.rgb = LINK_COLOR

    def _add_field(self, paragraph, name: str, r: Run, para_size: float, heading: bool) -> None:
        def fld(char_type: str):
            run = paragraph.add_run()
            self._style_run(run, r, para_size, heading)
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), char_type)
            run._element.append(el)

        fld("begin")
        instr_run = paragraph.add_run()
        self._style_run(instr_run, r, para_size, heading)
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = f" {name} "
        instr_run._element.append(instr)
        fld("separate")
        shown = paragraph.add_run(r.text)
        self._style_run(shown, r, para_size, heading)
        fld("end")

    def _add_runs(self, paragraph, runs: list[Run], para_size: float, heading: bool = False) -> None:
        i = 0
        while i < len(runs):
            r = runs[i]
            if r.field:
                self._add_field(paragraph, r.field, r, para_size, heading)
                i += 1
                continue
            if r.link:
                group = [r]
                while i + len(group) < len(runs) and runs[i + len(group)].link == r.link \
                        and not runs[i + len(group)].field:
                    group.append(runs[i + len(group)])
                self._add_hyperlink(paragraph, r.link, group, para_size, heading)
                i += len(group)
                continue
            run = paragraph.add_run(r.text)
            self._style_run(run, r, para_size, heading)
            i += 1

    def _add_hyperlink(self, paragraph, url: str, runs: list[Run], para_size: float, heading: bool) -> None:
        r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), r_id)
        for r in runs:
            run = paragraph.add_run(r.text)
            self._style_run(run, r, para_size, heading)
            link.append(run._element)  # moves the run inside the hyperlink element
        paragraph._p.append(link)

    # ------------------------------------------------------------------ blocks

    def _paragraph(self, p: Paragraph) -> None:
        doc = self.doc
        if p.kind == "heading":
            para = doc.add_paragraph(style=f"Heading {max(1, min(p.heading_level, 6))}")
        elif p.kind == "list" and p.list_type == "bullet":
            style = "List Bullet" if p.list_level == 0 else f"List Bullet {p.list_level + 1}"
            para = doc.add_paragraph(style=style)
        else:
            para = doc.add_paragraph()
        pf = para.paragraph_format
        para.alignment = ALIGN.get(p.align, WD_ALIGN_PARAGRAPH.LEFT)

        if p.kind == "list" and p.list_type == "number" and p.list_hanging:
            pf.left_indent = Pt(18 * (p.list_level + 1))
            pf.first_line_indent = Pt(-18)
            # Explicit tab stop: some renderers ignore the implicit hanging-indent stop.
            pf.tab_stops.add_tab_stop(Pt(18 * (p.list_level + 1)))
        elif self.mode == "layout" and p.kind != "list" and p.align in ("left", "justify"):
            if p.left_indent > 2:
                pf.left_indent = Pt(min(p.left_indent, 300))
            if abs(p.first_line_indent) > 2:
                pf.first_line_indent = Pt(max(min(p.first_line_indent, 200), -p.left_indent))

        if self.mode == "layout":
            pf.space_before = Pt(p.space_before)
            pf.space_after = Pt(0)
            if p.line_spacing:
                pf.line_spacing = p.line_spacing
        if p.page_break_before:
            pf.page_break_before = True

        sizes = [r.size for r in p.runs if r.text.strip()]
        para_size = max(set(sizes), key=sizes.count) if sizes else self.layout.body_size
        self._add_runs(para, p.runs, para_size, heading=p.kind == "heading")

    def _spacer(self, page_break: bool = False):
        """Empty paragraph separating tables (adjacent Word tables would fuse)."""
        para = self.doc.add_paragraph()
        para.paragraph_format.space_before = Pt(0)
        para.paragraph_format.space_after = Pt(0)
        if page_break:
            para.paragraph_format.page_break_before = True
        return para

    def _table(self, t: Table) -> None:
        n_rows, n_cols = len(t.rows), len(t.col_widths)
        if n_rows == 0 or n_cols == 0:
            return
        body = self.doc.element.body
        if t.page_break_before or (len(body) > 1 and body[-2].tag == qn("w:tbl")):
            self._spacer(page_break=t.page_break_before)
        table = self.doc.add_table(rows=n_rows, cols=n_cols)
        table.style = "Table Grid"
        table.autofit = False
        available = self._available_width()
        total = sum(t.col_widths) or 1.0
        scale = min(1.0, available / total)
        widths = [Pt(max(w * scale, 18)) for w in t.col_widths]
        for c, col in enumerate(table.columns):
            col.width = widths[c]
        for row in table.rows:
            for c, cell in enumerate(row.cells):
                cell.width = widths[c]
        self._explicit_table_props(table, sum(widths))

        for r, row in enumerate(t.rows):
            for c, cell in enumerate(row):
                if cell is None or (cell.row_span == 1 and cell.col_span == 1):
                    continue
                r_end = min(r + cell.row_span, n_rows) - 1
                c_end = min(c + cell.col_span, n_cols) - 1
                table.cell(r, c).merge(table.cell(r_end, c_end))

        for r, row in enumerate(t.rows):
            for c, cell in enumerate(row):
                if cell is None:
                    continue
                target = table.cell(r, c)
                for i, runs in enumerate(cell.paragraphs):
                    para = target.paragraphs[0] if i == 0 else target.add_paragraph()
                    para.paragraph_format.space_after = Pt(0)
                    para.paragraph_format.space_before = Pt(0)
                    sizes = [x.size for x in runs if x.text.strip()]
                    self._add_runs(para, runs, max(set(sizes), key=sizes.count) if sizes else self.layout.body_size)

        if all(cell is None or all(r.bold for p in cell.paragraphs for r in p if r.text.strip())
               for cell in t.rows[0]) and n_rows > 1:
            tr_pr = table.rows[0]._tr.get_or_add_trPr()
            header = OxmlElement("w:tblHeader")
            header.set(qn("w:val"), "true")
            tr_pr.append(header)
        self.tables_written += 1

    @staticmethod
    def _explicit_table_props(table, total_width_emu: int) -> None:
        """Write width and borders directly on the table.

        Word resolves them from the "Table Grid" style, but some readers (Quick Look,
        older Pages/LibreOffice builds) ignore table styles and auto widths.
        """
        tbl_pr = table._tbl.tblPr
        tbl_w = tbl_pr.find(qn("w:tblW"))
        if tbl_w is None:
            tbl_w = OxmlElement("w:tblW")
            tbl_pr.append(tbl_w)
        tbl_w.set(qn("w:type"), "dxa")
        tbl_w.set(qn("w:w"), str(int(total_width_emu / 635)))  # EMU -> twentieths of a point
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            el = OxmlElement(f"w:{edge}")
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), "4")
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), "000000")
            borders.append(el)
        # Schema order: tblStyle, ..., tblW, jc, ..., tblBorders, ..., tblLayout, ...
        layout = tbl_pr.find(qn("w:tblLayout"))
        if layout is not None:
            layout.addprevious(borders)
        else:
            tbl_pr.append(borders)

    def _image(self, im: Image) -> None:
        available = self._available_width()
        w = im.bbox[2] - im.bbox[0]
        h = im.bbox[3] - im.bbox[1]
        if w <= 0 or h <= 0:
            return
        scale = min(1.0, available / w)
        para = self.doc.add_paragraph()
        para.alignment = ALIGN[im.align]
        if self.mode == "layout":
            para.paragraph_format.space_before = Pt(im.space_before)
        if im.page_break_before:
            para.paragraph_format.page_break_before = True
        try:
            para.add_run().add_picture(io.BytesIO(im.data), width=Pt(w * scale), height=Pt(h * scale))
            self.images_written += 1
        except Exception:
            log.warning("an image could not be embedded and was skipped")
            self.images_skipped += 1
            para._p.getparent().remove(para._p)

    def _header_footer(self, lines: list[HeaderFooterLine], container, style: str) -> None:
        for i, line in enumerate(lines):
            para = container.paragraphs[0] if i == 0 else container.add_paragraph()
            para.style = self.doc.styles[style]
            para.alignment = ALIGN.get(line.align, WD_ALIGN_PARAGRAPH.LEFT)
            sizes = [r.size for r in line.runs if r.text.strip()]
            self._add_runs(para, line.runs, sizes[0] if sizes else self.layout.body_size)

    # ------------------------------------------------------------------ main

    def write(self) -> bytes:
        self._setup()
        section_size = None
        for page in self.layout.pages:
            size = (round(page.width), round(page.height))
            if self.mode == "layout" and section_size and size != section_size and page.elements:
                section = self.doc.add_section(WD_SECTION.NEW_PAGE)
                self._configure_section(section, page.width, page.height)
                page.elements[0].page_break_before = False
            section_size = section_size or size
            if self.mode == "layout":
                section_size = size
            for el in page.elements:
                if isinstance(el, Paragraph):
                    self._paragraph(el)
                elif isinstance(el, Table):
                    self._table(el)
                elif isinstance(el, Image):
                    self._image(el)

        body = self.doc.element.body
        if len(body) > 1 and body[-2].tag == qn("w:tbl"):
            self._spacer()  # Word expects a paragraph after a trailing table

        # Later sections inherit ("link to previous") the first section's header/footer.
        first = self.doc.sections[0]
        self._header_footer(self.layout.header, first.header, "Header")
        self._header_footer(self.layout.footer, first.footer, "Footer")

        buf = io.BytesIO()
        self.doc.save(buf)
        return buf.getvalue()


def write_docx(layout: DocumentLayout, mode: str) -> tuple[bytes, DocxWriter]:
    writer = DocxWriter(layout, mode)
    return writer.write(), writer
