# HA AI Studio — Devices and Actions

HA AI Studio runs configured Actions and uses them to update virtual Devices. Each Device can publish multiple named measurements to Home Assistant through MQTT Discovery and refresh them on an interval. Home Assistant retains long-term history and owns follow-up automations. No workflow chains or template mapping engine are included.

## Local development

```sh
docker compose up -d --build
```

Open http://localhost:8099. Add real MQTT brokers in **Settings**, then create an Action with a name and type. Edit Input broker/topic and Output broker/topic (default: Same as input). Under **General**, select **Active** and click **Save** to enable the production subscription. Clear **Active** and save to stop it. Changes remain local to the editor until saved. **Message schemas** opens a side panel; **Test in Home Assistant** provides a copyable MQTT test automation.

Local state persists in private `.dev-data`, excluded from builds/packages. `.env` is optional: copy `.env.example` and supply `DEV_MQTT_HOST`, port, username, password and TLS to add a read-only broker. Recreate the container after changes. No HA API token is needed. Production discovers HA's MQTT service through Supervisor only when available; otherwise add a broker manually. MQTT v5 brokers are required. No simulation fallback exists.

Optional **Settings → edit broker → Enable MQTT Discovery** publishes only explicitly opted-in virtual Devices and their measurement/diagnostic sensors. Actions and Studio itself are not advertised as devices. Previously owned Action/Studio discovery topics are durably removed before new device publication. Input and Output remain independent.

The frontend design system, build layout and isolated browser checks are documented in [frontend development](ha_ai_studio/frontend/README.md). The production frontend is built inside Docker and served by the Python backend in the same container.

## Local Action development

The development server also mounts `LOCAL_ACTIONS_DIR` read-only (default: the sibling `ha-ai-studio-actions-ai-on-the-edge-meters` directory). In Settings → Action repositories add a **Local directory** source with path `.`. Create an instance and edit its configuration. After changing source files, save/discard edits, deactivate the instance, and select **Reload from source**. Wait for its environment to become Ready before reactivating against an isolated test broker. No Git push is required. Local sources are enabled only on the explicitly configured development server.

Actions may include a trusted custom configuration editor via `"editor": "ui/editor.js"` in their manifest. See the [plugin contract](ha_ai_studio/app/action_types/README.md) for the editor interface, source limits and trust model. Local development still uses real MQTT; browser checks must use synthetic fixtures.

## Install

After the first successful container release, add `https://github.com/ha-ai-studio/ha-ai-studio` under **Settings → Apps → Install app → ⋮ → Repositories**, then install **HA AI Studio** and open its web UI through Ingress. This requires Home Assistant with Supervisor on amd64. The repository and GHCR package must be public for installation without registry credentials. The app is marked experimental. Action types are installed from Git repositories in Settings. Echo is maintained in the separate `ha-ai-studio-actions-core` repository; existing bundled Echo instances remain supported without automatic migration.

Home Assistant pulls `ghcr.io/ha-ai-studio/ha-ai-studio:<version>` using the version in `ha_ai_studio/config.yaml`; it does not build the image on the device. Before the first successful tag workflow, that image does not exist. Repository metadata can become visible before a tag build finishes, so announce an update only after publication succeeds.

`python3 scripts/package.py` also creates an archive in `dist/`. To build that source locally inside HA instead of pulling the released image, extract `ha_ai_studio` into HA's local apps directory and remove the `image:` line from the extracted `config.yaml` before refreshing/installing. The source slug is `ha_ai_studio`; repository and local installations may have different full app IDs and data locations. Back up data before switching. Nothing is installed in live HA by local development commands.

See [release instructions](docs/RELEASING.md).

Existing Agents are shown as **Unsupported · Disabled**. Their files, identities and credentials remain intact; create a new Echo Action explicitly. They are not converted. Deletion removes an Action from the active registry and stops subscriptions but retains files and reserves its ID; there is no restore UI.

See [the guide and HA automation examples](ha_ai_studio/DOCS.md), [plugin contract](ha_ai_studio/app/action_types/README.md) and [architecture](docs/ARCHITECTURE.md).

```sh
./scripts/test.sh
./scripts/test-ui.sh  # Frontend regressions; requires Node.js 22.21+; installs locked npm dependencies
./scripts/test-mqtt.sh
./scripts/test-mqtt-security.sh
python3 -m unittest discover -s tests -p test_package.py -v
./scripts/security-check.sh
python3 scripts/package.py
```

The MQTT check uses three temporary isolated Mosquitto brokers, never personal credentials. For local subpath testing run `docker compose --profile ingress up -d` and open http://localhost:8100/api/hassio_ingress/dev-check/. This is not a live HA authentication test.

CI builds the amd64 image once, running the React regression tests and TypeScript checks in its frontend build stage, then shares it as a workflow artifact with four parallel GitHub Actions matrix jobs: unit/API/package, MQTT, TLS/topic-permission and security. Each job has its own runner, logs and status; a failed suite does not cancel the others. Downloaded images are checked against the build job’s image ID. The final gate requires every suite to pass. Image artifacts are retained for seven days; rerunning failed jobs after expiry requires a full workflow rerun. For the same checks locally, run `docker compose build --quiet` followed by `python3 scripts/parallel_checks.py`. The local runner prints grouped logs and waits for every check; any failure blocks publication. Release CI pushes the exact tested image only after all checks pass. Security checks have separate steps for secrets, Python dependencies, Grype database updates and image scanning. CI caches only the Grype database (with daily cache keys), keeps automatic updates and age/hash validation enabled, and scans the downloaded image archive without exporting it again. Locally, `./scripts/security-check.sh` still runs every check; `SECURITY_GRYPE_CACHE_DIR` selects a cache directory and `SECURITY_IMAGE_ARCHIVE` optionally selects an existing image archive. `./scripts/test.sh` still builds by default; `--skip-build` uses the existing image.

## Repository layout

- `ha_ai_studio/`: installable HA app, Python runtime, React/TypeScript/SCSS frontend, dependency locks and user guide.
- `ha_ai_studio/app/action_types/`: explicitly registered Action plugins; currently Echo only.
- `tests/`: unit/API checks and isolated MQTT integration checks.
- `scripts/`: test runners, local Ingress proxy and source packaging.
- `docs/`: architecture and verification report.

`requirements.txt` lists direct dependencies; `requirements.lock.txt` pins the complete runtime environment used by Docker. Keep both when updating dependencies. The app directory is `ha_ai_studio/`, the Compose service is `studio`, and the development image is `ha-ai-studio:dev`.

Generated archives, browser captures and local caches do not belong in the source tree. Keep private `.env` and `.dev-data/` out of source control and packages. The packager includes only approved app files and rejects symlinks.

## Naming migration

This development copy uses the app slug `ha_ai_studio`, environment variables `HA_AI_STUDIO_DATA` and `HA_AI_STUDIO_DEV`, and request header `X-HA-AI-Studio-Request`. A changed app slug is a separate installation in Home Assistant: existing installations do not receive an automatic in-place upgrade. Back up and transfer app data explicitly before switching installations.

Changing stored MQTT identities changes Discovery entities and request/result topics. Recreate copied HA test automations using the current topics. Preserve the Discovery ownership manifest and its cleanup credentials until the old retained topics have been removed. Do not run two copies against the same data directory or identities.

## License

MIT — see [LICENSE](LICENSE). Third-party notices are listed in [THIRD_PARTY.md](ha_ai_studio/THIRD_PARTY.md).

## Automated checks

GitHub Actions runs unit/API tests, isolated MQTT integration tests, TLS and publish-ACL checks, reproducible packaging checks, full-history and working-tree secret scans, Python dependency auditing, and container vulnerability scanning. Security scanner images and checkout are pinned to immutable identifiers. Python advisories and High/Critical container findings fail the checks; scanner failures also fail closed. The separate release workflow publishes a versioned GHCR image only when a `vMAJOR.MINOR.PATCH` tag is pushed. After the image push, it creates a GitHub Release with AI-assisted English notes and the maintained changelog (changelog fallback if OpenAI is unavailable). See [release setup](docs/RELEASING.md) for the `OPENAI_API_KEY` Actions secret. It never deploys to Home Assistant.

The security check downloads public advisory databases and requires Internet access. All MQTT integration checks use disposable containers, generated test certificates and synthetic credentials. They never use `.env` or `.dev-data`.

Actual Supervisor/Ingress authentication and install/upgrade acceptance still require a separate Home Assistant test installation. The local subpath proxy does not verify HA authentication. No live HA installation is changed by these scripts.

The runtime uses the official Ubuntu 24.04 LTS container base, pinned by digest. Only Python, virtual-environment support and CA certificates are installed, without recommended packages. App dependencies live in `/opt/venv`; Ubuntu manages the system Python. Support remains limited to amd64.

## Virtual devices

Devices are the start page. Create a named Device without a preset device type, then compose it from up to 16 number or text readings. Each measurement selects an Action and a named reading; multiple measurements can share an Action. A device invokes each distinct Action once per update, serializes shared Action execution, and preserves successful measurements when another Action fails. Sensors have no predefined measurement type. Set a free unit, optionally mark a cumulative counter (value must not decrease), and optionally provide an HA `device_class` identifier in the Home Assistant metadata section. Studio treats this identifier as publication metadata and does not apply domain-specific rules. Counters use `total_increasing`; other numeric sensors use `measurement`. For HA water statistics, use unit `m³`, the counter option and HA metadata `water`.

Configure the camera, illumination and other input sources in the Action settings. Camera tests and device runs set dimmable illumination to 100% for capture, then restore the prior brightness and on/off state. Restoration is journaled for retry after failures or restart; switches and on/off-only lights retain their on/off behavior. Device measurement Actions must accept an empty input object and return named readings in their successful result data:

```json
{"readings": [{"id": "main", "status": "read", "value": 123.456, "unit": "m³"}]}
```

The existing Analog Meter Reader uses this contract. The UI offers its configured meter names; other compatible Actions use their documented reading ID. Device execution never uses documentation example payloads. If an Action declares a `previous` input property, Studio supplies the device's last accepted values and elapsed time for those readings.

**Save draft** does not change running configuration. **Activate device** snapshots the selected Actions' active configurations (or their drafts when MQTT is inactive), including bound source versions. Actions do not need MQTT activation for Device use. Deactivate dependent devices before reloading a local Action source. Saved device bindings protect referenced private images. Deleting a referenced Action is blocked until its device references are removed.

The device interval is 1–10080 minutes, measured from completion to the next update. Activation schedules the first reading after one interval; **Read now** runs the active configuration immediately. Missed intervals produce at most one current measurement, never a catch-up queue. Interrupted work is recorded and not replayed; a fresh measurement is scheduled after the next interval. Readings accept finite numbers or nonblank text of up to 255 characters. Text is preserved exactly, including leading zeros; configure an empty unit and HA device class and disable numeric checks for text. Only numeric values receive a statistics state class and are passed back as previous numeric readings. A sensor cannot switch between number and text after its first accepted reading; use a new sensor ID to protect history. The device sensor owns the unit; Action output units are ignored and values are not converted. Cumulative counter values cannot decrease or be negative; optionally set a maximum change per reading. A meter replacement requires a new sensor ID. Removed sensor IDs retain their previous values and publication metadata/unit constraints to protect historical statistics if reused.

Publication is a separate per-device opt-in and requires central Discovery to be enabled. Every published device has measurement, last-successful-reading timestamp and per-sensor status entities, plus device status. No root Studio device or Action devices are advertised. A disabled device remains visible with disabled diagnostics and its last accepted values. Last values and exact owned MQTT topics persist across restart. Discovery configuration and state snapshots are retained and replayed after HA birth/reconnect; Action request/result envelopes remain QoS 1, retain off. Failed publication retries without rerunning the Action. MQTT delivery is not exactly-once.

Deleting a device preserves its private stored record and queues exact-topic Discovery cleanup. Cleanup survives broker outages/restarts and must finish before broker/prefix migration; unrelated topics are never deleted. Home Assistant owns long-term statistics; Studio only keeps the latest accepted values and bounded Action execution records. Live HA authentication, actual HA entity registration and production TLS require separate integration validation.
