#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
network="studio-test-$$"
broker="studio-broker-$$"
broker2="studio-output-$$"
broker3="studio-discovery-$$"
cleanup() { docker rm -f "$broker" "$broker2" "$broker3" >/dev/null 2>&1 || true; docker network rm "$network" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
docker network create --internal "$network" >/dev/null
for entry in "$broker:broker" "$broker2:broker2" "$broker3:broker3"; do
  container=${entry%:*}
  alias=${entry#*:}
  docker run --rm -d --name "$container" --network "$network" --network-alias "$alias" eclipse-mosquitto:2 sh -c 'printf "listener 1883\nallow_anonymous true\npersistence false\n" > /tmp/test.conf; exec mosquitto -c /tmp/test.conf' >/dev/null
done
docker run --rm --platform linux/amd64 --network "$network" -e PYTHONPATH=/app -v "$PWD/tests:/tests:ro" "${STUDIO_TEST_IMAGE:-ha-ai-studio:dev}" python /tests/mqtt_broker_check.py

docker run --rm --platform linux/amd64 --network "$network" -e PYTHONPATH=/app -v "$PWD/tests:/tests:ro" "${STUDIO_TEST_IMAGE:-ha-ai-studio:dev}" python /tests/discovery_broker_check.py
