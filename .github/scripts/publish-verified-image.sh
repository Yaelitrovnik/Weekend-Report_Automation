#!/usr/bin/env bash
set -euo pipefail

repo="ghcr.io/${GITHUB_REPOSITORY,,}"
local_image="$1"
sha_tag="${repo}:sha-$2"
docker tag "$local_image" "$sha_tag"
docker push "$sha_tag"

version_tag="${repo}:$3"
docker tag "$local_image" "$version_tag"
docker push "$version_tag"

if [[ "${PUBLISH_LATEST}" == "1" ]]; then
  docker tag "$local_image" "${repo}:latest"
  docker push "${repo}:latest"
fi
