#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
case "${1:-}" in
  '') docker compose build --quiet ;;
  --skip-build) ;; # CI already built the exact image shared by all checks.
  *) echo 'Usage: ./scripts/test.sh [--skip-build]' >&2; exit 2 ;;
esac
docker run --rm --platform linux/amd64 -e PYTHONPATH=/app -v "$PWD/tests:/workspace/tests:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/ha_ai_studio:/workspace/ha_ai_studio:ro" -v "$PWD/LICENSE:/workspace/LICENSE:ro" "${STUDIO_TEST_IMAGE:-ha-ai-studio:dev}" python /workspace/scripts/unit_checks.py --group "${STUDIO_TEST_GROUP:-all}"
