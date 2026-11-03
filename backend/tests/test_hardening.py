"""Public-deployment hardening: rate limits, client identification, stats and feedback."""

from __future__ import annotations

import dataclasses
import sqlite3
import time

from fastapi.testclient import TestClient

from app import stats as stats_module
from app.clients import RateLimiter, client_key
from app.main import create_app
from test_api import upload, wait


def make_client(settings, **overrides):
    return TestClient(create_app(dataclasses.replace(settings, **overrides)))


# ---------------------------------------------------------------- rate limiting


def test_rate_limiter_windows_and_purge():
    limiter = RateLimiter({60: 2, 3600: 3})
    assert limiter.hit("a", now=0) is None
    assert limiter.hit("a", now=1) is None
    assert limiter.hit("a", now=2) == 59  # minute window full
    assert limiter.hit("b", now=2) is None  # other clients unaffected
    assert limiter.hit("a", now=61) is None
    assert limiter.hit("a", now=130) is not None  # hour window (3) full
    assert limiter.hit("a", now=130, record=False) is not None
    limiter.purge(now=10_000)
    assert limiter.events == {}


def test_disabled_limits_allow_everything():
    limiter = RateLimiter({3600: 0})
    assert all(limiter.hit("a") is None for _ in range(100))


def test_conversion_rate_limit_returns_429(settings, samples):
    with make_client(settings, conversions_per_hour=2) as client:
        for _ in range(2):
            job_id = upload(client, samples["text"].read_bytes()).json()["id"]
            wait(client, job_id)
        res = upload(client, samples["text"].read_bytes())
        assert res.status_code == 429
        assert res.json()["error"]["code"] == "rate_limited"
        assert int(res.headers["retry-after"]) > 0


def test_rejected_uploads_do_not_use_up_the_limit(settings, samples):
    with make_client(settings, conversions_per_hour=1) as client:
        assert upload(client, b"not a pdf", name="x.txt", content_type="text/plain").status_code == 415
        assert upload(client, samples["text"].read_bytes()).status_code == 202


def test_one_active_job_per_client(settings, samples):
    with make_client(settings, active_jobs_per_client=1) as client:
        first = upload(client, samples["multipage"].read_bytes())
        second = upload(client, samples["text"].read_bytes())
        assert first.status_code == 202
        assert second.status_code == 429
        assert second.json()["error"]["code"] == "too_many_active"
        wait(client, first.json()["id"])
        assert upload(client, samples["text"].read_bytes()).status_code == 202


def test_client_ip_header_only_trusted_when_enabled(settings, samples):
    pdf = samples["text"].read_bytes()

    def post(client, ip):
        return client.post("/api/convert", files={"file": ("a.pdf", pdf, "application/pdf")},
                           data={"mode": "layout"}, headers={"X-Client-IP": ip})

    with make_client(settings, conversions_per_hour=1, trust_client_ip_header=True) as client:
        assert post(client, "203.0.113.1").status_code == 202
        assert post(client, "203.0.113.1").status_code == 429
        assert post(client, "203.0.113.2").status_code == 202  # a different visitor

    with make_client(settings, conversions_per_hour=1, trust_client_ip_header=False) as client:
        assert post(client, "203.0.113.1").status_code == 202
        assert post(client, "203.0.113.2").status_code == 429  # spoofed header ignored


def test_ipv6_clients_grouped_by_prefix():
    assert client_key("2001:db8::1") == client_key("2001:db8::ffff")
    assert client_key("2001:db8::1") != client_key("2001:db8:0:1::1")
    assert client_key("203.0.113.1") != client_key("203.0.113.2")


# ---------------------------------------------------------------- stats & feedback


def test_stats_record_usage_without_personal_data(settings, samples, tmp_path, capsys, monkeypatch):
    db_path = tmp_path / "data" / "stats.db"
    with make_client(settings, stats_db=str(db_path), trust_client_ip_header=True) as client:
        for ip in ("203.0.113.9", "203.0.113.9", "198.51.100.4"):
            client.get("/api/health?visit=1", headers={"X-Client-IP": ip})
        client.get("/api/health")  # health checks are not visits

        ok = upload(client, samples["table"].read_bytes(), name="Secret Plan.pdf", mode="clean").json()["id"]
        assert wait(client, ok)["status"] == "done"
        bad = upload(client, samples["encrypted"].read_bytes()).json()["id"]
        assert wait(client, bad)["status"] == "failed"

        res = client.post("/api/feedback", json={"useful": True, "comment": "Great\x00 tables!", "mode": "clean",
                                                 "pages": 1})
        assert res.status_code == 204
        client.post("/api/feedback", json={"useful": False})

    report = stats_module.Stats(str(db_path)).report()
    day = report["daily"][0]
    assert day["visitors"] == 2
    assert day["uploads"] == 2 and day["mode_clean"] == 1 and day["mode_layout"] == 1
    assert day["converted"] == 1 and day["failed"] == 1 and day["pages"] == 1
    assert day["feedback_yes"] == 1 and day["feedback_no"] == 1
    assert report["errors"] == [{"code": "encrypted_pdf", "count": 1}]
    assert report["feedback"][1]["comment"] == "Great tables!"

    dump = "\n".join(sqlite3.connect(db_path).iterdump())
    for secret in ("203.0.113.9", "198.51.100.4", "Secret Plan", "Region", "North"):
        assert secret not in dump, f"{secret!r} must never be stored"

    monkeypatch.setenv("STATS_DB", str(db_path))
    assert stats_module.main([]) == 0
    out = capsys.readouterr().out
    assert "Found it useful: 1 of 2 answers" in out and "encrypted_pdf" in out


def test_feedback_validation_and_rate_limit(settings):
    with make_client(settings, feedback_per_hour=1) as client:
        assert client.post("/api/feedback", json={"useful": True, "comment": "x" * 1001}).status_code == 400
        assert client.post("/api/feedback", json={"comment": "no rating"}).status_code == 400
        assert client.post("/api/feedback", json={"useful": True}).status_code == 204
        assert client.post("/api/feedback", json={"useful": True}).status_code == 429


def test_stats_disabled_by_default(settings, samples):
    with make_client(settings) as client:
        assert client.get("/api/health?visit=1").status_code == 200
        assert client.post("/api/feedback", json={"useful": True}).status_code == 204
        time.sleep(0.05)
        assert not client.app.state.stats.enabled
