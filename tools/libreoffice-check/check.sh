#!/usr/bin/env sh
# Usage: tools/libreoffice-check/check.sh <dir-with-docx-files> <output-dir>
# Converts every .docx in the input directory to PDF using LibreOffice in Docker.
# A file that LibreOffice cannot open produces no PDF and makes the script fail.
set -eu
in_dir=$(cd "$1" && pwd)
out_dir=$(mkdir -p "$2" && cd "$2" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
docker build -q -t pdf2word-libreoffice-check "$here" >/dev/null
status=0
for f in "$in_dir"/*.docx; do
  name=$(basename "$f" .docx)
  docker run --rm --network none -v "$in_dir:/in:ro" -v "$out_dir:/out" pdf2word-libreoffice-check "/in/$name.docx" >/dev/null 2>&1 || true
  if [ -s "$out_dir/$name.pdf" ]; then echo "ok    $name.docx"; else echo "FAIL  $name.docx"; status=1; fi
done
exit $status
