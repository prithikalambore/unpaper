"""Converter-level tests: extraction, structure, formatting and DOCX validity."""

from __future__ import annotations

import io
import zipfile

import pymupdf
import pytest
from docx import Document

import generate_samples
from app.converter import convert_pdf, ocr
from app.converter.verify import verify_docx
from app.errors import ConversionError


def convert(path, out, settings, mode="layout"):
    result = convert_pdf(path, out, mode, settings)
    return result, Document(out)


def body_texts(doc):
    return [p.text for p in doc.paragraphs if p.text.strip()]


# ---------------------------------------------------------------- text & structure


@pytest.mark.parametrize("mode", ["layout", "clean"])
def test_text_paragraphs_headings_and_order(samples, out, settings, mode):
    result, doc = convert(samples["text"], out, settings, mode)
    texts = body_texts(doc)
    assert texts[0] == "Annual Report 2026"
    assert "Introduction" in texts and "Key Points" in texts
    assert texts.index("Introduction") < texts.index("Key Points")
    styles = {p.text: p.style.name for p in doc.paragraphs}
    assert styles["Annual Report 2026"] == "Heading 1"
    assert styles["Introduction"] == "Heading 2"
    # Lines of the wrapped paragraph are joined into one editable paragraph.
    para = next(t for t in texts if t.startswith("The quarterly review"))
    assert para.endswith("monitor closely.")
    assert result.pages == 1 and result.characters > 300


def test_bold_italic_and_fonts_preserved_in_layout_mode(samples, out, settings):
    _, doc = convert(samples["text"], out, settings, "layout")
    italic = next(p for p in doc.paragraphs if p.text.startswith("This paragraph is written in italic"))
    assert all(r.italic for r in italic.runs if r.text.strip())
    mixed = next(p for p in doc.paragraphs if p.text.startswith("Bold statement"))
    bold_runs = [r.text.strip() for r in mixed.runs if r.bold]
    assert "Bold statement:" in bold_runs
    assert any(not r.bold and "regular text" in r.text for r in mixed.runs)
    title = doc.paragraphs[0]
    assert title.alignment == 1  # centered
    assert title.runs[0].font.size.pt == 24
    assert title.runs[0].font.name == "Arial"  # Helvetica mapped to a cross-platform font


def test_lists(samples, out, settings):
    _, doc = convert(samples["text"], out, settings)
    bullets = [p.text for p in doc.paragraphs if p.style.name == "List Bullet"]
    assert bullets == ["Revenue grew by twelve percent", "Costs remained flat", "Two new offices opened"]
    numbered = [p.text for p in doc.paragraphs if p.text[:2] in ("1.", "2.", "3.")]
    assert numbered == ["1. Collect the data", "2. Review the results", "3. Publish the report"]


def test_hyperlink_is_a_real_word_link(samples, out, settings):
    convert(samples["text"], out, settings)
    with zipfile.ZipFile(out) as zf:
        xml = zf.read("word/document.xml").decode()
        rels = zf.read("word/_rels/document.xml.rels").decode()
    assert "<w:hyperlink" in xml
    assert 'Target="https://example.com/report"' in rels and 'TargetMode="External"' in rels


def test_two_column_reading_order(samples, out, settings):
    _, doc = convert(samples["two_column"], out, settings)
    text = "\n".join(body_texts(doc))
    assert text.index("LEFT COLUMN START") < text.index("LEFT COLUMN END") < text.index("RIGHT COLUMN START")
    assert text.index("RIGHT COLUMN START") < text.index("RIGHT COLUMN END")


def test_headers_footers_and_page_breaks(samples, out, settings):
    _, doc = convert(samples["multipage"], out, settings, "layout")
    texts = body_texts(doc)
    assert not any("Confidential" in t for t in texts), "running header must leave the body"
    assert not any(t.startswith("Page ") for t in texts)
    section = doc.sections[0]
    assert section.header.paragraphs[0].text == "Contoso Ltd - Confidential"
    with zipfile.ZipFile(out) as zf:
        footer_xml = "".join(zf.read(n).decode() for n in zf.namelist() if n.startswith("word/footer"))
    assert " PAGE " in footer_xml  # live page-number field, not a frozen "1"
    breaks = [p.text for p in doc.paragraphs if p.paragraph_format.page_break_before]
    assert breaks == ["Chapter 2", "Chapter 3"]


def test_clean_mode_has_no_page_breaks_or_fixed_fonts(samples, out, settings):
    _, doc = convert(samples["multipage"], out, settings, "clean")
    assert not any(p.paragraph_format.page_break_before for p in doc.paragraphs)
    body = [p for p in doc.paragraphs if p.style.name == "Normal" and p.text.strip()]
    assert all(r.font.size is None and r.font.name is None for p in body for r in p.runs)


# ---------------------------------------------------------------- tables


def test_table_becomes_editable_word_table(samples, out, settings):
    result, doc = convert(samples["table"], out, settings)
    assert result.tables == 1 and len(doc.tables) == 1
    rows = [[c.text for c in r.cells] for r in doc.tables[0].rows]
    assert rows == [["Region", "Q1", "Q2"], ["North", "120", "135"], ["South", "98", "110"], ["West", "143", "150"]]
    texts = body_texts(doc)
    assert "North" not in " ".join(texts), "table text must not be duplicated as paragraphs"
    assert texts == ["Sales by Region", "The table below lists the sales figures per region.",
                     "Figures are in thousands of euros."]
    header_cell = doc.tables[0].rows[0].cells[0].paragraphs[0]
    assert header_cell.runs[0].bold


def test_table_merged_cells(samples, out, settings):
    _, doc = convert(samples["merged_table"], out, settings)
    table = doc.tables[0]
    first_row = table.rows[0].cells
    assert first_row[0].text == "Quarterly Totals"
    assert first_row[0]._tc is first_row[1]._tc is first_row[2]._tc  # one merged cell
    assert [c.text for c in table.rows[1].cells] == ["Item", "Q1", "Q2"]


# ---------------------------------------------------------------- images


def test_images_are_embedded(samples, out, settings):
    result, doc = convert(samples["image"], out, settings)
    assert result.images == 1 and len(doc.inline_shapes) == 1
    shape = doc.inline_shapes[0]
    assert abs(shape.width.pt - 240) < 2 and abs(shape.height.pt - 160) < 2
    assert "Figure 1: A generated sample image." in body_texts(doc)


# ---------------------------------------------------------------- scanned PDFs / OCR


@pytest.mark.skipif(not ocr.ocr_available(), reason="Tesseract language data not installed")
def test_scanned_pdf_with_ocr(samples, out, settings):
    result, doc = convert(samples["scanned"], out, settings)
    text = " ".join(body_texts(doc)).lower()
    assert "invoice" in text and "payment" in text
    assert result.ocr_pages == 1
    assert len(doc.inline_shapes) == 0, "the scan bitmap is replaced by editable text"
    assert any("OCR" in w for w in result.warnings)


def test_scanned_pdf_without_ocr_fails_clearly(samples, out, settings, monkeypatch):
    monkeypatch.setattr(ocr, "ocr_available", lambda *a, **k: False)
    with pytest.raises(ConversionError) as exc:
        convert_pdf(samples["scanned"], out, "layout", settings)
    assert exc.value.code == "ocr_unavailable"
    assert not out.exists()


def test_mixed_document_without_ocr_keeps_scan_as_image(tmp_path, out, settings, monkeypatch):
    monkeypatch.setattr(ocr, "ocr_available", lambda *a, **k: False)
    doc = pymupdf.open()
    doc.insert_pdf(pymupdf.open(stream=generate_samples.make_text_pdf()))
    doc.insert_pdf(pymupdf.open(stream=generate_samples.make_scanned_pdf()))
    path = tmp_path / "mixed.pdf"
    doc.save(path)
    result = convert_pdf(path, out, "layout", settings)
    assert result.images == 1
    assert any("OCR is unavailable" in w for w in result.warnings)


# ---------------------------------------------------------------- invalid input


@pytest.mark.parametrize("name, code", [
    ("blank", "no_content"),
    ("encrypted", "encrypted_pdf"),
    ("malformed", "malformed_pdf"),
])
def test_invalid_pdfs_raise_clear_errors(samples, out, settings, name, code):
    with pytest.raises(ConversionError) as exc:
        convert_pdf(samples[name], out, "layout", settings)
    assert exc.value.code == code
    assert not out.exists(), "no document may be produced for a failed conversion"


def test_not_a_pdf(tmp_path, out, settings):
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"this is plain text, not a PDF")
    with pytest.raises(ConversionError) as exc:
        convert_pdf(path, out, "layout", settings)
    assert exc.value.code == "malformed_pdf"


def test_page_limit(samples, out, settings):
    import dataclasses
    limited = dataclasses.replace(settings, max_pages=2)
    with pytest.raises(ConversionError) as exc:
        convert_pdf(samples["multipage"], out, "layout", limited)
    assert exc.value.code == "too_many_pages"


# ---------------------------------------------------------------- DOCX validity


@pytest.mark.parametrize("name", ["text", "table", "merged_table", "multipage", "two_column", "image"])
@pytest.mark.parametrize("mode", ["layout", "clean"])
def test_every_output_is_a_valid_nonempty_docx(samples, out, settings, name, mode):
    convert_pdf(samples[name], out, mode, settings)
    data = out.read_bytes()
    stats = verify_docx(data)
    assert stats.characters > 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
        core = zf.read("docProps/core.xml").decode()
    assert "python-docx" not in core, "template metadata must not leak into output"


def test_verify_rejects_corrupt_and_empty_documents():
    with pytest.raises(ConversionError) as exc:
        verify_docx(b"PK\x03\x04 broken zip")
    assert exc.value.code == "invalid_output"
    buf = io.BytesIO()
    Document().save(buf)
    with pytest.raises(ConversionError) as exc:
        verify_docx(buf.getvalue())
    assert exc.value.code == "no_content"


def test_paragraph_split_across_pages_is_rejoined(tmp_path, out, settings):
    doc = pymupdf.open()
    first = doc.new_page()
    first.insert_text((72, 760), "This sentence starts on the first page and", fontname="helv", fontsize=11)
    second = doc.new_page()
    second.insert_text((72, 80), "continues on the second page.", fontname="helv", fontsize=11)
    second.insert_text((72, 110), "A new paragraph.", fontname="helv", fontsize=11)
    path = tmp_path / "split.pdf"
    doc.save(path)
    _, result = convert(path, out, settings)
    texts = body_texts(result)
    assert texts == ["This sentence starts on the first page and continues on the second page.",
                     "A new paragraph."]
