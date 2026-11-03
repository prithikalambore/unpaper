"""API tests: the full upload -> process -> download flow, errors and cleanup."""

from __future__ import annotations

import dataclasses
import io
import time

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def upload(client, data: bytes, name="report.pdf", mode="layout", content_type="application/pdf"):
    return client.post("/api/convert", files={"file": (name, data, content_type)}, data={"mode": mode})


def wait(client, job_id, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["status"] in ("done", "failed"):
            return status
        time.sleep(0.2)
    raise AssertionError("job did not finish in time")


def job_dirs(settings):
    return [p for p in settings.work_dir.iterdir() if p.is_dir()]


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert isinstance(body["ocr_available"], bool)
    assert body["limits"]["max_upload_mb"] == 25


def test_full_conversion_flow_and_cleanup(client, samples, settings):
    res = upload(client, samples["table"].read_bytes(), name="Sales Report.pdf")
    assert res.status_code == 202
    job_id = res.json()["id"]

    status = wait(client, job_id)
    assert status["status"] == "done", status
    assert status["progress"] == 100
    assert status["result"]["tables"] == 1
    # The uploaded PDF is deleted as soon as conversion finishes.
    assert not any((d / "input.pdf").exists() for d in job_dirs(settings))

    dl = client.get(f"/api/jobs/{job_id}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert 'filename="Sales Report.docx"' in dl.headers["content-disposition"]
    assert dl.headers["cache-control"] == "no-store"
    doc = Document(io.BytesIO(dl.content))
    assert len(doc.tables) == 1

    # Downloading removes the result from the server.
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert job_dirs(settings) == []


def test_clean_mode_via_api(client, samples):
    job_id = upload(client, samples["text"].read_bytes(), mode="clean").json()["id"]
    assert wait(client, job_id)["result"]["mode"] == "clean"


@pytest.mark.parametrize("sample, code", [
    ("malformed", "malformed_pdf"),
    ("encrypted", "encrypted_pdf"),
    ("blank", "no_content"),
])
def test_failed_jobs_report_errors_and_delete_files(client, samples, settings, sample, code):
    job_id = upload(client, samples[sample].read_bytes()).json()["id"]
    status = wait(client, job_id)
    assert status["status"] == "failed"
    assert status["error"]["code"] == code
    assert status["error"]["message"]
    assert job_dirs(settings) == [], "temporary files must be deleted on failure"
    assert client.get(f"/api/jobs/{job_id}/download").status_code == 409


def test_rejects_non_pdf_extension(client):
    res = upload(client, b"%PDF-1.7 fake", name="notes.txt", content_type="text/plain")
    assert res.status_code == 415
    assert res.json()["error"]["code"] == "invalid_file_type"


def test_rejects_pdf_name_with_wrong_content(client, settings):
    res = upload(client, b"MZ\x90\x00 an executable pretending to be a pdf", name="evil.pdf")
    assert res.status_code == 415
    assert job_dirs(settings) == []


def test_rejects_empty_file(client):
    res = upload(client, b"", name="empty.pdf")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "empty_file"


def test_rejects_oversized_file(settings):
    small = dataclasses.replace(settings, max_upload_mb=1)
    with TestClient(create_app(small)) as client:
        res = upload(client, b"%PDF-1.7\n" + b"0" * (2 * 1024 * 1024))
        assert res.status_code == 413
        assert res.json()["error"]["code"] == "file_too_large"


def test_rejects_invalid_mode_and_missing_file(client, samples):
    assert upload(client, samples["text"].read_bytes(), mode="fancy").status_code == 400
    res = client.post("/api/convert", data={"mode": "layout"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "missing_file"


def test_unknown_job(client):
    res = client.get("/api/jobs/does-not-exist")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "job_not_found"


def test_cancel_deletes_job(client, samples, settings):
    job_id = upload(client, samples["multipage"].read_bytes()).json()["id"]
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    time.sleep(0.5)
    assert job_dirs(settings) == []


def test_timeout_kills_worker(settings, samples):
    strict = dataclasses.replace(settings, conversion_timeout_s=0)
    with TestClient(create_app(strict)) as client:
        job_id = upload(client, samples["text"].read_bytes()).json()["id"]
        status = wait(client, job_id)
        assert status["status"] == "failed"
        assert status["error"]["code"] == "conversion_timeout"
        assert job_dirs(strict) == []


def test_expired_jobs_are_purged(settings, samples):
    quick = dataclasses.replace(settings, job_ttl_s=0)
    app = create_app(quick)
    with TestClient(app) as client:
        job_id = upload(client, samples["text"].read_bytes()).json()["id"]
        assert wait(client, job_id)["status"] == "done"
        time.sleep(0.01)
        assert app.state.jobs.purge_expired() == 1
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
        assert job_dirs(quick) == []
