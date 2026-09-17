#!/usr/bin/env bash
set -euo pipefail

docker image inspect --format '{{.Id}}' "$1" | tee image-id.txt
