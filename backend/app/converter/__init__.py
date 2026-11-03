"""Deterministic PDF -> DOCX conversion (no network access, no LLMs)."""

from .pipeline import ConversionResult, convert_pdf

__all__ = ["ConversionResult", "convert_pdf"]
