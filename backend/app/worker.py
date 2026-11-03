"""Conversion worker, executed in a separate process per job.

Running each conversion in its own process means a malformed PDF that
crashes or hangs the PDF engine cannot take the API down, the job can be
killed on timeout, and (on Linux) memory can be capped with rlimits.

The worker communicates through small JSON files in the job directory:
  progress.json - {"stage", "current", "total"} (updated during conversion)
  result.json   - ConversionResult on success
  error.json    - {"code", "message", "status"} on failure
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

INPUT_NAME = "input.pdf"
OUTPUT_NAME = "output.docx"


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def _limit_resources(memory_mb: int) -> None:
    if not sys.platform.startswith("linux") or memory_mb <= 0:
        return  # RLIMIT_AS is not enforced reliably on macOS
    import resource

    limit = memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def run_job(job_dir: str, mode: str, memory_mb: int) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s worker: %(message)s")
    directory = Path(job_dir)
    src = directory / INPUT_NAME
    try:
        _limit_resources(memory_mb)
        # Imported here so the parent process never loads the PDF engine for a job.
        from .config import get_settings
        from .converter import convert_pdf
        from .errors import ConversionError, conversion_failed

        def progress(stage: str, current: int, total: int) -> None:
            _write_json(directory / "progress.json", {"stage": stage, "current": current, "total": total})

        try:
            result = convert_pdf(src, directory / OUTPUT_NAME, mode, get_settings(), progress)
            _write_json(directory / "result.json", result.to_dict())
        except ConversionError as exc:
            _write_json(directory / "error.json", {**exc.to_dict(), "status": exc.status})
        except MemoryError:
            _write_json(directory / "error.json", {
                "code": "resource_limit", "status": 413,
                "message": "The document needs more memory than this server allows.",
            })
        except Exception as exc:
            # Log only the exception type: messages can contain document text.
            logging.getLogger(__name__).error("conversion failed: %s", type(exc).__name__)
            err = conversion_failed()
            _write_json(directory / "error.json", {**err.to_dict(), "status": err.status})
    finally:
        src.unlink(missing_ok=True)
