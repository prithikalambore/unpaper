"""In-memory job manager: queueing, per-job worker processes, timeouts and cleanup.

Privacy guarantees implemented here:
  * every job gets its own 0700 temp directory under WORK_DIR;
  * the uploaded PDF is deleted as soon as the worker finishes (or fails);
  * the whole job directory is deleted after download, on cancel, on failure,
    and by a janitor once JOB_TTL_SECONDS have passed;
  * WORK_DIR is wiped on startup to remove leftovers from a crash.

Jobs live in process memory, so the API must run as a single process
(one uvicorn worker). Scale by running more containers behind sticky routing.
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from . import errors
from .config import Settings
from .stats import Stats
from .worker import INPUT_NAME, OUTPUT_NAME, run_job

log = logging.getLogger(__name__)

STAGE_PROGRESS = {"queued": 0, "opening": 3, "extracting": 5, "analysing": 85, "writing": 90, "verifying": 96}
CHUNK = 1024 * 1024


@dataclass
class Job:
    id: str
    directory: Path
    mode: str
    filename: str
    size: int
    status: str = "queued"  # queued | processing | done | failed
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    error: dict | None = None
    result: dict | None = None
    process: mp.Process | None = None
    cancelled: bool = False
    client: str = ""  # opaque per-process client token (never an IP)


class JobManager:
    def __init__(self, settings: Settings, stats: Stats | None = None):
        self.settings = settings
        self.stats = stats or Stats("")
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.slots = threading.Semaphore(settings.max_concurrent_jobs)
        self.ctx = mp.get_context("spawn")  # never fork a threaded server
        self._stop = threading.Event()
        self._janitor: threading.Thread | None = None
        self.cleanup_hooks: list = []  # extra periodic housekeeping (e.g. rate-limiter purges)

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        root = self.settings.work_dir
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._janitor = threading.Thread(target=self._janitor_loop, daemon=True, name="job-janitor")
        self._janitor.start()

    def stop(self) -> None:
        self._stop.set()
        with self.lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            self._kill(job)
            shutil.rmtree(job.directory, ignore_errors=True)
        self.jobs.clear()

    # ------------------------------------------------------------------ submit

    def submit(self, stream: BinaryIO, filename: str, mode: str, client: str = "") -> Job:
        with self.lock:
            active = [j for j in self.jobs.values() if j.status in ("queued", "processing")]
            if len(active) >= self.settings.max_queued_jobs:
                raise errors.ConversionError(
                    "server_busy", "The server is busy converting other documents. Please try again shortly.", 503
                )
            per_client = self.settings.active_jobs_per_client
            if client and per_client and sum(1 for j in active if j.client == client) >= per_client:
                raise errors.ConversionError(
                    "too_many_active",
                    "You already have a conversion in progress. Wait for it to finish, then try again.", 429,
                )
        directory = Path(tempfile.mkdtemp(prefix="job-", dir=self.settings.work_dir))
        try:
            size = self._store_upload(stream, directory / INPUT_NAME)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        job = Job(id=uuid.uuid4().hex, directory=directory, mode=mode, filename=filename, size=size, client=client)
        with self.lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True, name=f"job-{job.id[:8]}").start()
        log.info("job %s accepted (%d bytes, mode=%s)", job.id, size, mode)
        return job

    def _store_upload(self, stream: BinaryIO, target: Path) -> int:
        limit = self.settings.max_upload_bytes
        size = 0
        head = b""
        with open(target, "wb") as out:
            while chunk := stream.read(CHUNK):
                if not head:
                    head = chunk[:1024]
                size += len(chunk)
                if size > limit:
                    raise errors.file_too_large(self.settings.max_upload_mb)
                out.write(chunk)
        if size == 0:
            raise errors.empty_file()
        if b"%PDF-" not in head:
            raise errors.invalid_file_type()
        target.chmod(0o600)
        return size

    # ------------------------------------------------------------------ execution

    def _run(self, job: Job) -> None:
        with self.slots:
            if job.cancelled:
                return
            self._update(job, status="processing")
            proc = self.ctx.Process(
                target=run_job, args=(str(job.directory), job.mode, self.settings.worker_memory_mb), daemon=True
            )
            job.process = proc
            started = time.time()
            proc.start()
            proc.join(self.settings.conversion_timeout_s)
            timed_out = proc.is_alive()
            if timed_out:
                self._kill(job)
            job.process = None

        (job.directory / INPUT_NAME).unlink(missing_ok=True)
        if job.cancelled:
            return
        elapsed = time.time() - started
        if timed_out:
            self._fail(job, errors.conversion_timeout(self.settings.conversion_timeout_s).to_dict())
        elif (job.directory / "result.json").exists() and (job.directory / OUTPUT_NAME).exists():
            result = json.loads((job.directory / "result.json").read_text())
            self._update(job, status="done", result=result)
            self.stats.converted(result.get("pages", 0), result.get("ocr_pages", 0), elapsed)
            log.info("job %s done in %.1fs (%d pages)", job.id, elapsed, result.get("pages", 0))
        elif (job.directory / "error.json").exists():
            err = json.loads((job.directory / "error.json").read_text())
            self._fail(job, {"code": err.get("code"), "message": err.get("message")})
        else:  # worker died without reporting (crash, OOM kill)
            self._fail(job, errors.conversion_failed().to_dict())

    def _fail(self, job: Job, error: dict) -> None:
        shutil.rmtree(job.directory, ignore_errors=True)
        self._update(job, status="failed", error=error)
        self.stats.failed(str(error.get("code")))
        log.info("job %s failed: %s", job.id, error.get("code"))

    def _kill(self, job: Job) -> None:
        proc = job.process
        if proc is not None and proc.is_alive():
            proc.terminate()
            proc.join(5)
            if proc.is_alive():
                proc.kill()
                proc.join(5)

    def _update(self, job: Job, **changes) -> None:
        with self.lock:
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated = time.time()

    # ------------------------------------------------------------------ queries

    def get(self, job_id: str) -> Job:
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            raise errors.ConversionError(
                "job_not_found", "This conversion was not found. It may have expired - please convert the file again.",
                404,
            )
        return job

    def status(self, job_id: str) -> dict:
        job = self.get(job_id)
        payload = {"id": job.id, "status": job.status, "mode": job.mode, "filename": job.filename,
                   "stage": job.status, "progress": 0, "current_page": 0, "total_pages": 0}
        if job.status == "processing":
            stage, current, total = "opening", 0, 0
            try:
                data = json.loads((job.directory / "progress.json").read_text())
                stage, current, total = data["stage"], data["current"], data["total"]
            except (OSError, ValueError, KeyError):
                pass
            pct = STAGE_PROGRESS.get(stage, 3)
            if stage == "extracting" and total:
                pct = 5 + int(80 * current / total)
            payload.update(stage=stage, progress=pct, current_page=current, total_pages=total)
        elif job.status == "done":
            payload.update(stage="done", progress=100, result=job.result)
        elif job.status == "failed":
            payload.update(stage="failed", error=job.error)
        return payload

    def output_path(self, job_id: str) -> Path:
        job = self.get(job_id)
        path = job.directory / OUTPUT_NAME
        if job.status != "done" or not path.exists():
            raise errors.ConversionError("not_ready", "The converted document is not available.", 409)
        return path

    def discard(self, job_id: str) -> None:
        """Delete a job and all of its files (after download, or on user cancel)."""
        with self.lock:
            job = self.jobs.pop(job_id, None)
        if job is None:
            return
        job.cancelled = True
        self._kill(job)
        shutil.rmtree(job.directory, ignore_errors=True)

    # ------------------------------------------------------------------ janitor

    def purge_expired(self) -> int:
        now = time.time()
        with self.lock:
            expired = [j.id for j in self.jobs.values()
                       if j.status in ("done", "failed") and now - j.updated > self.settings.job_ttl_s]
        for job_id in expired:
            self.discard(job_id)
        return len(expired)

    def _janitor_loop(self) -> None:
        while not self._stop.wait(30):
            try:
                self.purge_expired()
                for hook in self.cleanup_hooks:
                    hook()
            except Exception:  # keep the janitor alive no matter what
                log.exception("janitor error")
