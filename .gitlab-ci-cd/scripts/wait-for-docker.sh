#!/bin/sh
set -e

until docker info >/dev/null 2>&1; do
  sleep 1
done
