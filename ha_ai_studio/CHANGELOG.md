## 0.0.19

- Add bounded, read-only sensor history access for trusted Actions using server-owned Home Assistant credentials.
- Support sensor selection with the shared entity picker, inline search and clear controls, and numeric draft defaults.
- Integrate the Edge-Meter Leak detection Action with Device sensors and Execute functions while preserving measurement schedules.
- Add synthetic history, runner, Device-function and UI tests. No Action is installed, activated or published automatically.

## 0.0.18

- Export saved Device configurations as versioned JSON templates, including sensors, derived rates, functions and measurement intervals.
- Import templates with explicit assignments to existing Actions and a configurable Device name.
- Create imported Devices with new identities, inactive and unpublished; exclude Home Assistant identities, Action settings, credentials and measurement history.
- Use compact icon buttons with descriptive tooltips for editing, exporting and deleting Devices.

## 0.0.17

- Show preparation progress while an Action's Python environment is being installed or verified; open configuration automatically once it is ready.
- Keep installation details and environment recovery accessible when setup fails.
- Add derived rate-of-change Device sensors and improve spacing in sensor configuration.

## 0.0.16

### Device functions

- Add named Execute functions to Devices independently of sensors, with automatic Home Assistant MQTT buttons when the Device is published.
- Execute the selected Action using its saved binding and update only its sensors on that Device; preserve the automatic measurement schedule.
- Support function-only Devices, explicit publication, durable cleanup, retained-command rejection and restart-safe deduplication of the last 256 function request IDs per Device.

## 0.0.15

- Check Git and GitHub Release repositories for updates without changing existing Action instances.
- Download repository versions side by side and retain a version history with release or commit links.
- Select downloaded Action versions in General, including downgrades, while preserving identities, settings and request history.
- Preview and apply repository-wide Action updates with inactive/idle guards, schema validation and atomic binding persistence.
- Keep direct filesystem reloads for local development Actions and allow failed local imports to be retried.
- Add compact repository controls with descriptive icons and tooltips, plus a right-aligned Apply version control.
- Extend regression coverage for update detection, history migration, restart, downgrade, bulk updates and persistence failures.

## 0.0.14

- Add configurable virtual Devices with independent measurement schedules, multiple sensors and optional Home Assistant MQTT discovery.
- Compact Device settings and sensor editing, with live measurement logs and clearer status information.
- Control device operation with an Active checkbox applied by Save; keep the Save button at the end of the form.
- Preserve specific reading and alignment failures and publish a Status Message diagnostic entity for each sensor.
- Preserve last accepted readings on failure and clear diagnostic errors after successful recovery.
- Extend coverage for device settings, persistence, sensor identity and isolated MQTT discovery diagnostics.

## 0.0.13

- Fix a timing-dependent MQTT integration test failure after Action restart by waiting for the background subscription instead of opening a competing connection.
- Add regression coverage ensuring restart establishes only one input connection, including delayed connection setup.

## 0.0.12

- Keep release-source integration tests in the container test suite instead of the pre-build release-tooling checks.
- Fix the release workflow failing before image creation because the app runtime is unavailable on the host runner.

## 0.0.11

- Add GitHub Release sources with latest stable or explicit stable tag selection.
- Validate built Action packages and SHA256 checksums before importing immutable snapshots.
- Preserve installed Action versions across catalog refresh and restart.

## 0.0.10

- Add Home Assistant entity search and scoped camera/illumination access for Actions.
- Support reference image capture and live camera progress with wait diagnostics.
- Support an optional discarded warm-up frame and local HA connection overrides.

## 0.0.9

- Add private image assets and isolated previews for complex Actions.
- Extend custom editors with image upload and preview capabilities.
- Include Tesseract for local number-block recognition.

## 0.0.8

- Upgrade checkout, artifact upload/download and cache Actions to releases that run natively on Node.js 24 in both CI workflows.
- Pin all four Actions to verified immutable commit hashes and annotate their release versions.

The app remains experimental and amd64-only. Live Home Assistant authentication and production TLS require separate validation.

## 0.0.7

- Cache the Grype vulnerability database between CI runs while keeping automatic updates and database age and integrity validation enabled.
- Scan the downloaded build image archive directly instead of exporting the image again.
- Show secret scanning, Python dependency auditing, database updates and image scanning as separate Security checks steps.
- Preserve the local security-check entry point and add regression tests for archive reuse, missing archives and failure propagation.

The app remains experimental and amd64-only. Live Home Assistant authentication and production TLS require separate validation.

## 0.0.6

- Run unit/API/package, MQTT, TLS/topic-permission and security checks as separate parallel GitHub Actions jobs with individual logs and results.
- Build the amd64 image once and share it between jobs as a workflow artifact; verify its image ID before testing and publication.
- Require every check to pass before packaging or publishing, while allowing other test suites to finish after a failure.
- Limit release write permissions to the publishing job and document artifact retention and workflow reruns.

The app remains experimental and amd64-only. Live Home Assistant authentication and production TLS require separate validation.

## 0.0.5

- Migrate the frontend to React, TypeScript and an SCSS design system, with regression tests and type checks in the container build.
- Run unit/API/package, isolated MQTT, TLS/topic-permission and security checks in parallel on the built image; publish only after all checks succeed.
- Isolate Git schema checks in cancellable worker processes with CPU, memory, concurrency and wall-time limits so expensive schemas cannot block the Studio server.
- Add HTTP liveness, validation recovery and worker cleanup regression coverage.

The app remains experimental and amd64-only. Live Home Assistant authentication and production TLS require separate validation.

## 0.0.4

- Update the release workflow repository guard to the ha-ai-studio organization so release jobs run after the repository transfer.
- Publish container images under ghcr.io/ha-ai-studio/ha-ai-studio and align app metadata, repository URLs, OCI source labels and installation documentation.
- Add a regression check for consistent repository identity across release inputs.

The app remains experimental and amd64-only. The schema-validation limitation documented for 0.0.3 still applies.

## 0.0.3

- Install dynamic action types from Git repositories into isolated per-instance Python environments with pinned commits and locked dependencies.
- Move new Echo instances to the separate Actions Core repository while preserving existing bundled Echo instances and their identities.
- Include Actions Core as a protected System repository; allow deletion of unused user repositories.
- Show installation details from the Actions table, with status, version, logs and environment rebuild controls in a side panel.
- Align Settings cards and use consistent borderless repository action buttons.
- Fix environment executable lookup, manifest message schemas, optional numeric settings and visible installation errors.
- Bound subprocess output and cleanup, validate execute signatures before Ready, and validate request examples before offering Home Assistant test templates.
- Add Python and frontend regression coverage for repository lifecycle, isolated execution and failure recovery.

Known limitation: plugin schema validation still runs in the server process. Certain regular expressions can block the app; bounded schema validation remains an open high-priority issue. This release remains experimental and amd64-only.

## 0.0.2

- Add app store branding with an icon, logo and an introduction to AI Actions.
- Generate English release summaries and highlights using OpenAI, with the maintained changelog as a fallback when the API is unavailable or not configured.
- Publish GitHub Releases after successful container publication, including installation instructions and a link to the source changes.
- Mark experimental releases as pre-releases and document secure API key configuration through GitHub Actions secrets.

The app remains experimental and amd64-only. Echo is the only implemented Action type; AI release-note generation runs in CI, not in the application.

## 0.0.1

First experimental release of HA AI Studio.

- Configure Echo Actions with independent MQTT input/output brokers and validated JSON message contracts.
- Follow incoming requests, processing and outgoing replies in the per-Action live activity view.
- Use optional Home Assistant MQTT Discovery with a stable installation identity and durable cleanup.
- Configure brokers and inspect connection diagnostics through the Home Assistant-style Ingress UI.
- Persist request IDs and results, reject retained requests and route loops, and drain MQTT operations safely during shutdown.
- Run on Ubuntu 24.04 LTS; amd64 is the only supported architecture.
- Publish versioned GHCR images only from version tags after tests and security checks.
- Include MIT licensing and reproducible source archives without local filesystem metadata.

Only Echo is implemented; no AI inference model is included yet. Actual HA installation and upgrade acceptance remain to be validated before a stable release.
