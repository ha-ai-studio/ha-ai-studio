#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
case "${1:-}" in
  '') docker compose build --quiet ;;
  --skip-build) ;; # CI already built the exact image shared by all checks.
  *) echo 'Usage: ./scripts/test.sh [--skip-build]' >&2; exit 2 ;;
esac
set --
if [ -d "${STUDIO_LOCAL_ACTIONS_DIR:-../ha-ai-studio-actions-ai-on-the-edge-meters}" ]; then
  set -- -v "${STUDIO_LOCAL_ACTIONS_DIR:-$PWD/../ha-ai-studio-actions-ai-on-the-edge-meters}:/workspace/local-actions:ro"
fi
docker run --rm --platform linux/amd64 -v "$PWD/tests:/workspace/tests:ro" -v "$PWD/scripts:/workspace/scripts:ro" -v "$PWD/ha_ai_studio:/workspace/ha_ai_studio:ro" "$@" -v "$PWD/LICENSE:/workspace/LICENSE:ro" "${STUDIO_TEST_IMAGE:-ha-ai-studio:dev}" python -m unittest discover -s /workspace/tests -v
