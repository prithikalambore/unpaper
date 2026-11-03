"""Validate a generated .docx before it is offered for download."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

from docx import Document

from ..errors import invalid_output, no_content

REQUIRED_PARTS = ("[Content_Types].xml", "_rels/.rels", "word/document.xml")


@dataclass
class DocxStats:
    paragraphs: int
    characters: int
    tables: int
    images: int


def verify_docx(data: bytes) -> DocxStats:
    """Raise ConversionError unless `data` is a readable, non-empty Word document."""
    if not data:
        raise invalid_output()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if zf.testzip() is not None:
                raise invalid_output()
            names = set(zf.namelist())
            if not all(part in names for part in REQUIRED_PARTS):
                raise invalid_output()
        doc = Document(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise invalid_output() from exc
    except Exception as exc:
        if getattr(exc, "code", None):
            raise
        raise invalid_output() from exc

    chars = sum(len(p.text.strip()) for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                chars += len(cell.text.strip())
    stats = DocxStats(
        paragraphs=len(doc.paragraphs),
        characters=chars,
        tables=len(doc.tables),
        images=len(doc.inline_shapes),
    )
    if stats.characters == 0 and stats.images == 0:
        raise no_content()
    return stats
