"""Map PDF font names to font families that Word, Pages and LibreOffice understand.

PDF fonts are usually embedded subsets with PostScript names such as
``ABCDEF+TimesNewRomanPS-BoldItalicMT``. Word documents reference fonts by
family name and rely on the reader's installed fonts, so we strip subset
prefixes and style suffixes and map common PostScript families to widely
installed equivalents.
"""

from __future__ import annotations

import re

# PostScript family (lower-case, no spaces/dashes) -> family name in Word
_KNOWN = {
    "helvetica": "Arial",
    "helveticaneue": "Helvetica Neue",
    "arial": "Arial",
    "arialmt": "Arial",
    "arialnarrow": "Arial Narrow",
    "times": "Times New Roman",
    "timesroman": "Times New Roman",
    "timesnewroman": "Times New Roman",
    "timesnewromanps": "Times New Roman",
    "timesnewromanpsmt": "Times New Roman",
    "courier": "Courier New",
    "couriernew": "Courier New",
    "couriernewpsmt": "Courier New",
    "calibri": "Calibri",
    "cambria": "Cambria",
    "cambriamath": "Cambria Math",
    "georgia": "Georgia",
    "verdana": "Verdana",
    "tahoma": "Tahoma",
    "trebuchetms": "Trebuchet MS",
    "garamond": "Garamond",
    "palatino": "Palatino Linotype",
    "palatinolinotype": "Palatino Linotype",
    "bookantiqua": "Book Antiqua",
    "centurygothic": "Century Gothic",
    "segoeui": "Segoe UI",
    "symbol": "Symbol",
    "zapfdingbats": "Wingdings",
    "wingdings": "Wingdings",
    "dejavusans": "DejaVu Sans",
    "dejavuserif": "DejaVu Serif",
    "liberationsans": "Arial",
    "liberationserif": "Times New Roman",
    "liberationmono": "Courier New",
    "opensans": "Open Sans",
    "roboto": "Roboto",
    "lato": "Lato",
    "sourcesanspro": "Source Sans Pro",
    "glyphlessfont": "",  # OCR text layer: fall back to the document default
}

_STYLE_WORDS = (
    "bolditalic", "boldoblique", "semibold", "demibold", "extrabold", "ultrabold",
    "black", "heavy", "bold", "italic", "oblique", "light", "medium", "regular",
    "roman", "book", "condensed", "narrow", "thin", "mt", "ps", "psmt",
)

_BOLD_HINTS = ("bold", "black", "heavy", "semibold", "demibold", "extrabold")
_ITALIC_HINTS = ("italic", "oblique")


def strip_subset(name: str) -> str:
    return re.sub(r"^[A-Z]{6}\+", "", name or "")


def is_bold_name(name: str) -> bool:
    lower = strip_subset(name).lower()
    return any(h in lower for h in _BOLD_HINTS)


def is_italic_name(name: str) -> bool:
    lower = strip_subset(name).lower()
    return any(h in lower for h in _ITALIC_HINTS)


def word_font_name(pdf_name: str) -> str:
    """Return a Word font family for a PDF font name ('' means 'use default')."""
    name = strip_subset(pdf_name)
    if not name:
        return ""
    family = re.split(r"[-,]", name, maxsplit=1)[0]
    key = re.sub(r"[\s_]", "", family).lower()
    if key in _KNOWN:
        return _KNOWN[key]
    trimmed = key
    changed = True
    while changed:
        changed = False
        for word in _STYLE_WORDS:
            if trimmed.endswith(word) and len(trimmed) > len(word):
                trimmed = trimmed[: -len(word)]
                changed = True
    if trimmed in _KNOWN:
        return _KNOWN[trimmed]
    # Unknown family: split CamelCase ("MinionPro" -> "Minion Pro") and keep it.
    # Readers without the font substitute a similar one.
    pretty = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", family).strip()
    pretty = re.sub(r"(MT|PS|PSMT)$", "", pretty).strip()
    return pretty[:31]  # Word limits font names to 31 characters
