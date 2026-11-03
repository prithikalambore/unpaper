"""End-to-end conversion: PDF file -> validated DOCX file."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Literal

import pymupdf

from .. import errors
from ..config import Settings
from . import ocr
from .docx_writer import write_docx
from .extract import RawPage, extract_page
from .layout import analyse
from .verify import verify_docx

log = logging.getLogger(__name__)

Mode = Literal["layout", "clean"]
ProgressFn = Callable[[str, int, int], None]

# Keep MuPDF diagnostics out of stderr/logs; they can quote object data.
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)


@dataclass
class ConversionResult:
    pages: int
    mode: str
    ocr_pages: int = 0
    tables: int = 0
    images: int = 0
    characters: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _noop(stage: str, current: int, total: int) -> None:  # pragma: no cover - trivial
    pass


def convert_pdf(src: Path, dst: Path, mode: Mode, settings: Settings, progress: ProgressFn = _noop) -> ConversionResult:
    if mode not in ("layout", "clean"):
        raise errors.ConversionError("invalid_mode", "Unknown conversion mode.", 400)

    progress("opening", 0, 0)
    try:
        doc = pymupdf.open(src, filetype="pdf")
    except Exception as exc:
        raise errors.malformed_pdf() from exc

    with doc:
        if doc.needs_pass:
            raise errors.encrypted_pdf()
        try:
            n_pages = doc.page_count
        except Exception as exc:
            raise errors.malformed_pdf() from exc
        if n_pages == 0:
            raise errors.malformed_pdf()
        if n_pages > settings.max_pages:
            raise errors.too_many_pages(settings.max_pages)

        allow_ocr = ocr.ocr_available(settings.ocr_language)
        raw_pages: list[RawPage] = []
        failed_pages = 0
        for i in range(n_pages):
            progress("extracting", i + 1, n_pages)
            try:
                page = doc.load_page(i)
                raw_pages.append(
                    extract_page(page, ocr_language=settings.ocr_language,
                                 ocr_dpi=settings.ocr_dpi, allow_ocr=allow_ocr)
                )
            except Exception as exc:
                failed_pages += 1
                log.warning("page %d could not be processed (%s)", i + 1, type(exc).__name__)
        if failed_pages == n_pages:
            raise errors.malformed_pdf()
        title = ""
        try:
            meta_title = (doc.metadata or {}).get("title") or ""
            if meta_title and not meta_title.lower().startswith("microsoft word"):
                title = meta_title.strip()
        except Exception:
            pass

    warnings: list[str] = []
    total_chars = sum(p.char_count for p in raw_pages)
    total_images = sum(len(p.images) for p in raw_pages)
    scanned_no_ocr = [p for p in raw_pages if p.looks_scanned and not p.ocr_used and not p.ocr_found_nothing]
    ocr_empty = [p for p in raw_pages if p.ocr_found_nothing]
    ocr_pages = sum(1 for p in raw_pages if p.ocr_used)

    if total_chars == 0:
        if scanned_no_ocr and not allow_ocr:
            raise errors.ocr_unavailable()
        if ocr_empty:
            raise errors.ocr_failed()
        if total_images == 0:
            raise errors.no_content()

    if failed_pages:
        warnings.append(f"{failed_pages} page(s) were damaged and could not be converted.")
    if scanned_no_ocr:
        warnings.append(
            f"{len(scanned_no_ocr)} page(s) look scanned but OCR is unavailable; "
            "they were kept as images and are not editable text."
        )
    if ocr_empty:
        warnings.append(f"OCR found no text on {len(ocr_empty)} scanned page(s); they were kept as images.")
    if ocr_pages:
        warnings.append(
            f"Text on {ocr_pages} scanned page(s) was recognised with OCR. Please proofread it, "
            "as OCR can misread characters."
        )
    if any(p.skipped_rotated for p in raw_pages):
        warnings.append("Some rotated or vertical text could not be converted and was left out.")

    progress("analysing", n_pages, n_pages)
    layout = analyse(raw_pages, mode, title)

    progress("writing", n_pages, n_pages)
    data, writer = write_docx(layout, mode)
    if writer.images_skipped:
        warnings.append(f"{writer.images_skipped} image(s) could not be embedded.")

    progress("verifying", n_pages, n_pages)
    stats = verify_docx(data)
    if total_chars > 0 and stats.characters == 0:
        raise errors.invalid_output()

    tmp = dst.with_suffix(".part")
    tmp.write_bytes(data)
    os.replace(tmp, dst)
    return ConversionResult(
        pages=n_pages, mode=mode, ocr_pages=ocr_pages, tables=stats.tables, images=stats.images,
        characters=stats.characters, warnings=warnings,
    )
