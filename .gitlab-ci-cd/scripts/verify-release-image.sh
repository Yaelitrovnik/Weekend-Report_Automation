#!/bin/sh
set -e

RELEASE_IMAGE_TAG="$1"

RELEASE_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$RELEASE_IMAGE_TAG")"
BUILT_IMAGE_ID="$(cat image-id.txt)"

if [ "$RELEASE_IMAGE_ID" != "$BUILT_IMAGE_ID" ]; then
  echo "Release tag does not point to the exact smoked image" >&2
  echo "build=$BUILT_IMAGE_ID" >&2
  echo "release=$RELEASE_IMAGE_ID" >&2
  exit 1
fi

printf '%s\n' "$RELEASE_IMAGE_ID" > release-image-id.txt
