"""Small unit tests for helpers."""

from __future__ import annotations

import pytest

from app.converter.fonts import is_bold_name, is_italic_name, word_font_name
from app.converter.spans import clean_text
from app.filenames import content_disposition, output_filename


@pytest.mark.parametrize("pdf_name, expected", [
    ("ABCDEF+TimesNewRomanPS-BoldItalicMT", "Times New Roman"),
    ("Helvetica-Bold", "Arial"),
    ("ArialMT", "Arial"),
    ("CourierNewPSMT", "Courier New"),
    ("Calibri,Bold", "Calibri"),
    ("XYZABC+MinionPro-Regular", "Minion Pro"),
    ("GlyphLessFont", ""),
    ("", ""),
])
def test_font_mapping(pdf_name, expected):
    assert word_font_name(pdf_name) == expected


def test_style_from_font_name():
    assert is_bold_name("Arial-BoldMT") and not is_italic_name("Arial-BoldMT")
    assert is_italic_name("Helvetica-Oblique")


def test_clean_text_strips_xml_illegal_chars_and_maps_bullets():
    assert clean_text("a\x00b\x0bc\uf0b7") == "abc\u2022"


@pytest.mark.parametrize("original, expected", [
    ("Report.pdf", "Report.docx"),
    ("Q3 results FINAL.PDF", "Q3 results FINAL.docx"),
    ("../../etc/passwd.pdf", "passwd.docx"),
    ("C:\\Users\\me\\secret plan.pdf", "secret plan.docx"),
    ("inv<a>lid|na*me?.pdf", "inv_a_lid_na_me.docx"),
    ("Résumé – 2026.pdf", "Résumé – 2026.docx"),
    (".pdf", "converted-document.docx"),
    ("", "converted-document.docx"),
    ("a" * 300 + ".pdf", "a" * 100 + ".docx"),
])
def test_output_filename_is_sanitised(original, expected):
    assert output_filename(original) == expected


def test_content_disposition_has_ascii_fallback_and_utf8():
    header = content_disposition("Résumé.docx")
    assert 'filename="Rsum.docx"' in header
    assert "filename*=UTF-8''R%C3%A9sum%C3%A9.docx" in header
    assert 'filename="converted-document.docx"' in content_disposition("报告.docx")
