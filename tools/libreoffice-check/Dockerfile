# Renders .docx files to PDF with LibreOffice (Debian packages) to check that
# converted documents open and lay out correctly in an independent editor.
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-writer-nogui fonts-liberation fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home lo
USER lo
WORKDIR /work
ENTRYPOINT ["soffice", "--headless", "--norestore", "--convert-to", "pdf", "--outdir", "/out"]
