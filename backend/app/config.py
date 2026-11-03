"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _list(name: str, default: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 25))
    max_pages: int = field(default_factory=lambda: _int("MAX_PAGES", 300))
    conversion_timeout_s: int = field(default_factory=lambda: _int("CONVERSION_TIMEOUT_SECONDS", 300))
    max_concurrent_jobs: int = field(default_factory=lambda: _int("MAX_CONCURRENT_JOBS", 2))
    max_queued_jobs: int = field(default_factory=lambda: _int("MAX_QUEUED_JOBS", 20))
    job_ttl_s: int = field(default_factory=lambda: _int("JOB_TTL_SECONDS", 600))
    worker_memory_mb: int = field(default_factory=lambda: _int("WORKER_MEMORY_MB", 4096))
    ocr_language: str = field(default_factory=lambda: os.environ.get("OCR_LANGUAGE", "eng"))
    ocr_dpi: int = field(default_factory=lambda: _int("OCR_DPI", 300))
    work_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("WORK_DIR", Path(tempfile.gettempdir()) / "pdf2word"))
    )
    allowed_origins: list[str] = field(
        default_factory=lambda: _list("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    )
    # Abuse limits per visitor (keyed by client IP; see app/clients.py). 0 disables a limit.
    conversions_per_hour: int = field(default_factory=lambda: _int("RATE_LIMIT_PER_HOUR", 20))
    conversions_per_day: int = field(default_factory=lambda: _int("RATE_LIMIT_PER_DAY", 60))
    active_jobs_per_client: int = field(default_factory=lambda: _int("MAX_ACTIVE_JOBS_PER_CLIENT", 1))
    feedback_per_hour: int = field(default_factory=lambda: _int("FEEDBACK_PER_HOUR", 10))
    # Only enable when the backend is reachable solely through the bundled frontend proxy,
    # which sets X-Client-IP from the reverse proxy's X-Forwarded-For.
    trust_client_ip_header: bool = field(
        default_factory=lambda: os.environ.get("TRUST_CLIENT_IP_HEADER", "").lower() in ("1", "true", "yes")
    )
    # SQLite file for anonymous usage counters and feedback; empty disables stats.
    stats_db: str = field(default_factory=lambda: os.environ.get("STATS_DB", ""))

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def get_settings() -> Settings:
    return Settings()
