# Local development and verification

[Getting started](../README.md)

## Local development

Before starting, isolate the checkout with its own image tag, Compose project,
host ports and data directory. `compose.yaml` hard-codes the default image and
ports, so a unique project name alone is insufficient. Use a checkout-specific
Compose override and start with:

```sh
docker compose -p <project> -f compose.yaml -f <override.yaml> up -d --build
```

Inspect image IDs and mount paths before testing and verify the image contains
this checkout's code. Pin that image for the test run. Never reuse another
checkout's private state or stop its containers to free a port.

The default URL is `http://localhost:8099` when that port belongs exclusively to this checkout. Add real MQTT brokers in **Settings**, then create an Action with a name and type. Edit Input broker/topic and Output broker/topic (default: Same as input). Under **General**, select **Active** and click **Save** to enable the production subscription. Clear **Active** and save to stop it. Changes remain local to the editor until saved. **Message schemas** opens a side panel; **Test in Home Assistant** provides a copyable MQTT test automation.

Local state persists in private `.dev-data`, excluded from builds/packages. `.env` is optional: copy `.env.example` and supply `DEV_MQTT_HOST`, port, username, password and TLS to add a read-only broker. Recreate the container after changes. MQTT does not require an HA API token. Camera/entity access during local development uses the explicitly configured `HA_URL` and `HA_ACCESS_TOKEN`; keep these private. Production discovers HA's MQTT service through Supervisor only when available; otherwise add a broker manually. MQTT v5 brokers are required. No simulation fallback exists.

Optional **Settings → edit broker → Enable MQTT Discovery** publishes only explicitly opted-in virtual Devices and their sensors, diagnostics and configured function buttons. Actions and Studio itself are not advertised as devices. Previously owned Action/Studio discovery topics are durably removed before new device publication. Input and Output remain independent.

The frontend design system, build layout and isolated browser checks are documented in [frontend development](../ha_ai_studio/frontend/README.md). The production frontend is built inside Docker and served by the Python backend in the same container.

## Local Action development

The development server also mounts `LOCAL_ACTIONS_DIR` read-only (default: the sibling `ha-ai-studio-actions-ai-on-the-edge-meters` directory). In Settings → Action repositories add a **Local directory** source with path `.`. Create an instance and edit its configuration. After changing source files, save/discard edits, deactivate the instance and its dependent Devices, wait for running work to finish, and select **Reload from source**. Wait for its environment to become Ready before reactivating against an isolated test broker. No Git push is required. Local sources are enabled only on the explicitly configured development server.

Actions may include a trusted custom configuration editor via `"editor": "ui/editor.js"` in their manifest. See the [plugin contract](../ha_ai_studio/app/action_types/README.md) for the editor interface, source limits and trust model. Local development still uses real MQTT; browser checks must use synthetic fixtures.

The Edge-Meter repository also provides **Leak detection**, which reads a cumulative
water sensor's HA history. It requires Studio 0.0.19 or newer. Configure its
`leak-status` / `leak-reason` readings on a Device and optionally add an Execute
function. Notifications and follow-up actions remain in Home Assistant.
For cross-repository integration tests, set `STUDIO_LOCAL_ACTIONS_DIR` to the
Edge-Meter repository root when running `scripts/test.sh`; gateway tests also run
without that optional source checkout.

## Repository layout

- `ha_ai_studio/`: installable HA app, Python runtime, React/TypeScript/SCSS frontend, dependency locks and user guide.
- `ha_ai_studio/app/action_types/`: Action source management, plugin runners, contracts and the compatibility-only legacy Echo runtime.
- `tests/`: unit/API checks and isolated MQTT integration checks.
- `scripts/`: test runners, local Ingress proxy and source packaging.
- `docs/`: architecture and verification report.

`requirements.txt` lists direct dependencies; `requirements.lock.txt` pins the complete runtime environment used by Docker. Keep both when updating dependencies. The app directory is `ha_ai_studio/`, the Compose service is `studio`, and the development image is `ha-ai-studio:dev`.

Generated archives, browser captures and local caches do not belong in the source tree. Keep private `.env` and `.dev-data/` out of source control and packages. The packager includes only approved app files and rejects symlinks.

## Naming migration

This development copy uses the app slug `ha_ai_studio`, environment variables `HA_AI_STUDIO_DATA` and `HA_AI_STUDIO_DEV`, and request header `X-HA-AI-Studio-Request`. A changed app slug is a separate installation in Home Assistant: existing installations do not receive an automatic in-place upgrade. Back up and transfer app data explicitly before switching installations.

Changing stored MQTT identities changes Discovery entities and request/result topics. Recreate copied HA test automations using the current topics. Preserve the Discovery ownership manifest and its cleanup credentials until the old retained topics have been removed. Do not run two copies against the same data directory or identities.

## Automated checks

GitHub Actions runs unit/API tests, isolated MQTT integration tests, TLS and publish-ACL checks, reproducible packaging checks, full-history and working-tree secret scans, Python dependency auditing, and container vulnerability scanning. Security scanner images and checkout are pinned to immutable identifiers. Python advisories and High/Critical container findings fail the checks; scanner failures also fail closed. The separate release workflow publishes a versioned GHCR image only when a `vMAJOR.MINOR.PATCH` tag is pushed. After the image push, it creates a GitHub Release with AI-assisted English notes and the maintained changelog (changelog fallback if OpenAI is unavailable). See [release setup](RELEASING.md) for the `OPENAI_API_KEY` Actions secret. It never deploys to Home Assistant.

The security check downloads public advisory databases and requires Internet access. All MQTT integration checks use disposable containers, generated test certificates and synthetic credentials. They never use `.env` or `.dev-data`.

Actual Supervisor/Ingress authentication and install/upgrade acceptance still require a separate Home Assistant test installation. The local subpath proxy does not verify HA authentication. No live HA installation is changed by these scripts.

The runtime uses the official Ubuntu 24.04 LTS container base, pinned by digest. The Dockerfile installs Python, virtual-environment support, CA certificates, Git and Tesseract OCR with English data, without recommended packages. App dependencies live in `/opt/venv`; Ubuntu manages the system Python. Support remains limited to amd64.

## Running checks

After building and verifying the isolated image, set `STUDIO_TEST_IMAGE` to its
immutable image ID for the Python and MQTT suites:

```sh
STUDIO_TEST_IMAGE=<image-id> ./scripts/test.sh --skip-build
STUDIO_TEST_IMAGE=<image-id> ./scripts/test-mqtt.sh
./scripts/test-ui.sh
```

The UI suite requires Node.js 22.21+ and installs locked dependencies. Use the
embedded Codex browser for interactive checks, with disposable storage,
synthetic Actions and blocked MQTT/HA access. MQTT integration checks use
isolated brokers. Check each additional script's image options before running
it; do not fall back to a shared `ha-ai-studio:dev` image.

Release checks and publication are described in [RELEASING.md](RELEASING.md).
