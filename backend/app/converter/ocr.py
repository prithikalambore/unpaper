"""OCR support for image-only (scanned) pages.

PyMuPDF ships MuPDF's built-in Tesseract engine; it only needs the Tesseract
language data files (``tessdata``). OCR runs entirely locally - page images
are never sent to an external service.
"""

from __future__ import annotations

import functools
import logging
import os

import pymupdf

log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def tessdata_dir() -> str | None:
    """Return the tessdata directory, or None when OCR is unavailable."""
    try:
        path = pymupdf.get_tessdata()
    except Exception:  # raised when neither TESSDATA_PREFIX nor tesseract is found
        return None
    return path if path and os.path.isdir(path) else None


def ocr_available(language: str = "eng") -> bool:
    path = tessdata_dir()
    if not path:
        return False
    return all(os.path.isfile(os.path.join(path, f"{lang}.traineddata")) for lang in language.split("+"))


def ocr_textpage(page: pymupdf.Page, language: str, dpi: int) -> pymupdf.TextPage:
    """Run OCR over the whole rendered page and return a MuPDF text page."""
    return page.get_textpage_ocr(
        flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES & ~pymupdf.TEXT_PRESERVE_LIGATURES,
        language=language,
        dpi=dpi,
        full=True,
        tessdata=tessdata_dir(),
    )
