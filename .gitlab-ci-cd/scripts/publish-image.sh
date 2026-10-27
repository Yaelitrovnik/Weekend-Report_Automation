#!/bin/sh
set -e

RELEASE_IMAGE_TAG="$1"
SHORT_SHA="$2"
IMAGE_VERSION="$3"

if [ "${WEEKEND_REPORT_PUBLISH_IMAGE:-0}" = "1" ]; then
  test -n "${CI_REGISTRY:-}" || {
    echo "CI_REGISTRY is unavailable; cannot publish" >&2
    exit 1
  }

  printf '%s\n' "$CI_REGISTRY_PASSWORD" |
    docker login "$CI_REGISTRY" \
      -u "$CI_REGISTRY_USER" \
      --password-stdin

  SHA_TAG="${CI_REGISTRY_IMAGE}:sha-${SHORT_SHA}"
  docker tag "$RELEASE_IMAGE_TAG" "$SHA_TAG"
  docker push "$SHA_TAG"

  VERSION_TAG="${CI_REGISTRY_IMAGE}:${IMAGE_VERSION}"
  docker tag "$RELEASE_IMAGE_TAG" "$VERSION_TAG"
  docker push "$VERSION_TAG"

  if [ "${WEEKEND_REPORT_PUBLISH_LATEST:-0}" = "1" ]; then
    docker tag "$RELEASE_IMAGE_TAG" "${CI_REGISTRY_IMAGE}:latest"
    docker push "${CI_REGISTRY_IMAGE}:latest"
  fi
fi
