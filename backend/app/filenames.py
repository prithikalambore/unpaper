"""Filename sanitising for user-supplied names (never used as filesystem paths)."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

DEFAULT_STEM = "converted-document"
MAX_STEM = 100


def output_filename(original: str | None) -> str:
    """Build a safe, descriptive .docx download name from the uploaded file name."""
    name = unicodedata.normalize("NFKC", original or "")
    name = re.split(r"[\\/]", name)[-1]  # drop any client-side path
    name = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^\w\s.\-()&,+'\u2013\u2014]", "_", name)  # \w is unicode-aware: keeps accented letters
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r"\s+", " ", name).strip(" ._-")
    name = re.sub(r"_+", "_", name)
    name = name[:MAX_STEM].rstrip(" ._-")
    return f"{name or DEFAULT_STEM}.docx"


def content_disposition(filename: str) -> str:
    stem = filename[: -len(".docx")] if filename.endswith(".docx") else filename
    ascii_stem = stem.encode("ascii", "ignore").decode().replace('"', "").replace("\\", "").strip(" ._-")
    ascii_name = f"{ascii_stem or DEFAULT_STEM}.docx"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
