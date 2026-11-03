"""Typed errors with stable machine-readable codes and user-facing messages.

Messages must never include document content: they are shown to users and
may be logged.
"""

from __future__ import annotations


class ConversionError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


def invalid_file_type() -> ConversionError:
    return ConversionError("invalid_file_type", "Only PDF files can be converted. Please choose a .pdf file.", 415)


def file_too_large(limit_mb: int) -> ConversionError:
    return ConversionError("file_too_large", f"The file is larger than the {limit_mb} MB limit.", 413)


def empty_file() -> ConversionError:
    return ConversionError("empty_file", "The uploaded file is empty.", 400)


def malformed_pdf() -> ConversionError:
    return ConversionError(
        "malformed_pdf", "This PDF is damaged or not a valid PDF file and could not be read.", 422
    )


def encrypted_pdf() -> ConversionError:
    return ConversionError(
        "encrypted_pdf",
        "This PDF is password-protected. Remove the password and try again.",
        422,
    )


def too_many_pages(limit: int) -> ConversionError:
    return ConversionError("too_many_pages", f"PDFs with more than {limit} pages are not supported.", 422)


def no_content() -> ConversionError:
    return ConversionError(
        "no_content", "The PDF does not contain any text or images that could be converted.", 422
    )


def ocr_unavailable() -> ConversionError:
    return ConversionError(
        "ocr_unavailable",
        "This PDF appears to be scanned (it contains no selectable text), but OCR is not "
        "available on this server, so no editable text could be extracted.",
        422,
    )


def ocr_failed() -> ConversionError:
    return ConversionError(
        "ocr_failed",
        "This PDF appears to be scanned, but OCR could not recognise any text in it.",
        422,
    )


def conversion_timeout(seconds: int) -> ConversionError:
    return ConversionError(
        "conversion_timeout", f"The conversion took longer than {seconds} seconds and was stopped.", 504
    )


def conversion_failed() -> ConversionError:
    return ConversionError(
        "conversion_failed", "The document could not be converted due to an unexpected error.", 500
    )


def invalid_output() -> ConversionError:
    return ConversionError(
        "invalid_output",
        "The converted Word document failed validation, so it was not offered for download.",
        500,
    )
