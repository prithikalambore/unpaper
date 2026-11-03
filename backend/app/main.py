"""FastAPI application exposing the PDF -> Word conversion API.

Endpoints
  GET    /api/health                 service status, OCR availability and limits (?visit=1 counts a visit)
  POST   /api/feedback               {"useful": bool, "comment": str, "mode": str, "pages": int}
  POST   /api/convert                upload a PDF (multipart: file, mode) -> 202 + job id
  GET    /api/jobs/{id}              job status and progress
  GET    /api/jobs/{id}/download     the .docx (deleted from the server after download)
  DELETE /api/jobs/{id}              cancel a job / delete its files

All errors use the shape {"error": {"code": str, "message": str}}.
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import errors
from .clients import RateLimiter, client_ip, client_key
from .config import Settings, get_settings
from .converter.ocr import ocr_available
from .filenames import content_disposition, output_filename
from .jobs import JobManager
from .stats import Stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ACCEPTED_TYPES = {"application/pdf", "application/x-pdf", "application/octet-stream", ""}
MULTIPART_OVERHEAD = 64 * 1024
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class Feedback(BaseModel):
    useful: bool
    comment: str = Field(default="", max_length=1000)
    mode: str = Field(default="", max_length=10)
    pages: int = Field(default=0, ge=0, le=100_000)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    stats = Stats(settings.stats_db)
    manager = JobManager(settings, stats)
    convert_limiter = RateLimiter({3600: settings.conversions_per_hour, 86400: settings.conversions_per_day})
    feedback_limiter = RateLimiter({3600: settings.feedback_per_hour})
    manager.cleanup_hooks += [convert_limiter.purge, feedback_limiter.purge]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.start()
        log.info("ready (ocr_available=%s, max_upload_mb=%d)", ocr_available(settings.ocr_language),
                 settings.max_upload_mb)
        yield
        manager.stop()

    app = FastAPI(title="PDF to Word Converter", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.jobs = manager
    app.state.stats = stats
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )

    def error_response(code: str, message: str, status: int, headers: dict | None = None) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status,
                            headers={"Cache-Control": "no-store", **(headers or {})})

    def rate_limited(retry_after: int, message: str) -> JSONResponse:
        return error_response("rate_limited", message, 429, {"Retry-After": str(retry_after)})

    def key_for(request: Request) -> str:
        return client_key(client_ip(request, settings.trust_client_ip_header))

    @app.exception_handler(errors.ConversionError)
    async def conversion_error(_: Request, exc: errors.ConversionError):
        return error_response(exc.code, exc.message, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return error_response("invalid_request", "The request was missing required fields.", 400)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return error_response(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.error("unhandled error: %s", type(exc).__name__)
        return error_response("internal_error", "An unexpected server error occurred.", 500)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.get("/api/health")
    async def health(request: Request, visit: int = 0):
        if visit:
            stats.visit(key_for(request))
        return {
            "status": "ok",
            "ocr_available": ocr_available(settings.ocr_language),
            "limits": {"max_upload_mb": settings.max_upload_mb, "max_pages": settings.max_pages},
        }

    @app.post("/api/convert", status_code=202)
    async def convert(request: Request):
        length = request.headers.get("content-length")
        if length is None or not length.isdigit():
            return error_response("length_required", "The upload must include a Content-Length header.", 411)
        if int(length) > settings.max_upload_bytes + MULTIPART_OVERHEAD:
            raise errors.file_too_large(settings.max_upload_mb)

        client = key_for(request)
        wait = convert_limiter.hit(client, record=False)
        if wait:
            stats.rate_limited()
            return rate_limited(wait, "You've reached the limit for free conversions for now. "
                                      f"Please try again in {max(1, round(wait / 60))} minute(s).")

        form = await request.form(max_files=1, max_fields=4)
        try:
            upload = form.get("file")
            mode = str(form.get("mode") or "layout")
            if not isinstance(upload, UploadFile):
                return error_response("missing_file", "Please attach a PDF file.", 400)
            if mode not in ("layout", "clean"):
                return error_response("invalid_mode", "Mode must be 'layout' or 'clean'.", 400)
            name = upload.filename or ""
            if not name.lower().endswith(".pdf") or (upload.content_type or "") not in ACCEPTED_TYPES:
                raise errors.invalid_file_type()
            job = await run_in_threadpool(manager.submit, upload.file, output_filename(name), mode, client)
        finally:
            await form.close()  # removes Starlette's spooled temp copy of the upload
        convert_limiter.hit(client)
        stats.upload(mode)
        return {"id": job.id, "status": job.status}

    @app.post("/api/feedback", status_code=204)
    async def feedback(request: Request, body: Feedback):
        wait = feedback_limiter.hit(key_for(request))
        if wait:
            return rate_limited(wait, "Thanks — we've already received your feedback.")
        comment = CONTROL_CHARS.sub("", body.comment).strip()
        mode = body.mode if body.mode in ("layout", "clean") else ""
        stats.feedback(body.useful, comment, mode, body.pages)

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str):
        return JSONResponse(manager.status(job_id), headers={"Cache-Control": "no-store"})

    @app.get("/api/jobs/{job_id}/download")
    async def download(job_id: str):
        path = manager.output_path(job_id)
        job = manager.get(job_id)
        return FileResponse(
            path,
            media_type=DOCX_MIME,
            headers={"Content-Disposition": content_disposition(job.filename), "Cache-Control": "no-store"},
            background=BackgroundTask(manager.discard, job_id),
        )

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def cancel(job_id: str):
        manager.discard(job_id)

    return app


app = create_app()
