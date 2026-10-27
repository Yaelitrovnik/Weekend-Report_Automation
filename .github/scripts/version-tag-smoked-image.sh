#!/usr/bin/env bash
set -euo pipefail

docker tag "$1" "$2"
release_id="$(docker image inspect --format '{{.Id}}' "$2")"
build_id="$(cat image-id.txt)"
if [[ "${release_id}" != "${build_id}" ]]; then
  echo "Release tag does not point to the exact smoked image" >&2
  echo "build=${build_id}" >&2
  echo "release=${release_id}" >&2
  exit 1
fi
printf '%s\n' "${release_id}" > release-image-id.txt
