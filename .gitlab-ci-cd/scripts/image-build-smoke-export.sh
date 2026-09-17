#!/bin/sh
set -e

export SHORT_SHA="$(printf '%s' "$CI_COMMIT_SHA" | cut -c1-12)"

test -f TAG || { echo "TAG file is missing" >&2; exit 1; }
export IMAGE_VERSION="$(tr -d '\r\n' < TAG)"
printf '%s' "$IMAGE_VERSION" |
  grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$' ||
  { echo "TAG must contain a semantic version such as v1.0.2" >&2; exit 1; }

export WEEKEND_REPORT_BUILD_ID="gl-${CI_PIPELINE_ID}-${CI_JOB_ID}"
export WEEKEND_REPORT_APP_VERSION="$IMAGE_VERSION"
export LOCAL_IMAGE_TAG="weekend-report:ci-${SHORT_SHA}"
export RELEASE_IMAGE_TAG="weekend-report:${IMAGE_VERSION}"
export ARCHIVE_BASE="weekend-report_${IMAGE_VERSION}"

docker build --pull \
  --label "org.opencontainers.image.title=Weekend Report Automation" \
  --label "org.opencontainers.image.version=${IMAGE_VERSION}" \
  --label "org.opencontainers.image.revision=${CI_COMMIT_SHA}" \
  --label "org.opencontainers.image.source=${CI_PROJECT_URL}" \
  --label "io.weekend-report.build-id=${WEEKEND_REPORT_BUILD_ID}" \
  -t "$LOCAL_IMAGE_TAG" .

IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$LOCAL_IMAGE_TAG")"
printf '%s\n' "$IMAGE_ID" | tee image-id.txt

python scripts/ci.py image-smoke --image "$LOCAL_IMAGE_TAG"

docker tag "$LOCAL_IMAGE_TAG" "$RELEASE_IMAGE_TAG"

sh .gitlab-ci-cd/scripts/verify-release-image.sh "$RELEASE_IMAGE_TAG"
sh .gitlab-ci-cd/scripts/export-offline-image.sh "$RELEASE_IMAGE_TAG" "$ARCHIVE_BASE"
sh .gitlab-ci-cd/scripts/publish-image.sh "$RELEASE_IMAGE_TAG" "$SHORT_SHA" "$IMAGE_VERSION"