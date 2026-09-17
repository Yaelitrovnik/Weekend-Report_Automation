#!/bin/sh
set -e

RELEASE_IMAGE_TAG="$1"
ARCHIVE_BASE="$2"

mkdir -p dist

TAR_PATH="dist/${ARCHIVE_BASE}.tar"
docker save -o "$TAR_PATH" "$RELEASE_IMAGE_TAG"
gzip -1 -f "$TAR_PATH"

cd dist
sha256sum "${ARCHIVE_BASE}.tar.gz" > "${ARCHIVE_BASE}.tar.gz.sha256"
cd ..

cp image-id.txt dist/image-id.txt
cp release-image-id.txt dist/release-image-id.txt
