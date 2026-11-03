# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract language data for local OCR (PyMuPDF bundles the OCR engine itself).
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/* \
 && ln -s "$(dirname "$(find /usr/share/tesseract-ocr -name eng.traineddata | head -n1)")" /usr/share/tessdata
ENV TESSDATA_PREFIX=/usr/share/tessdata

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app

# ---- test image: docker build --target test -t pdf2word-backend-test . && docker run --rm pdf2word-backend-test
FROM base AS test
COPY requirements-dev.txt pyproject.toml ./
RUN pip install -r requirements-dev.txt
COPY scripts ./scripts
COPY tests ./tests
CMD ["python", "-m", "pytest", "-q", "-p", "no:warnings"]

# ---- runtime image (default)
FROM base AS runtime
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /tmp/pdf2word /data && chown appuser:appuser /tmp/pdf2word /data && chmod 700 /tmp/pdf2word /data
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
# One worker: job state lives in process memory (see app/jobs.py).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--timeout-keep-alive", "5", "--limit-concurrency", "50", "--no-server-header"]
