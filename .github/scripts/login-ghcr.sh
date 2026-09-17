#!/usr/bin/env bash
set -euo pipefail

echo "$GHCR_TOKEN" | docker login ghcr.io -u "${GITHUB_ACTOR}" --password-stdin
