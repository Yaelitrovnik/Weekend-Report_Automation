#!/usr/bin/env bash
set -euo pipefail

mkdir -p dist
archive="dist/$2.tar.gz"
docker save "$1" | gzip -1 > "$archive"
(cd dist && sha256sum "$2.tar.gz" > "$2.tar.gz.sha256")
cp image-id.txt dist/image-id.txt
cp release-image-id.txt dist/release-image-id.txt
