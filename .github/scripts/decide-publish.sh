#!/usr/bin/env bash
set -euo pipefail

publish=false

if [[ "${TAG_PUBLISH}" == "1" ]]; then
  publish=true
fi

echo "enabled=${publish}" >> "$GITHUB_OUTPUT"
