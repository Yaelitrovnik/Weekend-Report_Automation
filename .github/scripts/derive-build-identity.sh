#!/usr/bin/env bash
set -euo pipefail

short_sha="${GITHUB_SHA:0:12}"

if [[ ! -f TAG ]]; then
  echo "TAG file is missing" >&2
  exit 1
fi

version="$(tr -d '\r\n' < TAG)"

if [[ ! "${version}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
  echo "TAG must contain a semantic version such as v1.0.2" >&2
  exit 1
fi

build_id="gh-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
image_tag="weekend-report:ci-${short_sha}"
release_image_tag="weekend-report:${version}"
archive_base="weekend-report_${version}"

echo "short_sha=${short_sha}" >> "$GITHUB_OUTPUT"
echo "version=${version}" >> "$GITHUB_OUTPUT"
echo "build_id=${build_id}" >> "$GITHUB_OUTPUT"
echo "image_tag=${image_tag}" >> "$GITHUB_OUTPUT"
echo "release_image_tag=${release_image_tag}" >> "$GITHUB_OUTPUT"
echo "archive_base=${archive_base}" >> "$GITHUB_OUTPUT"
