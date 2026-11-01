# Project rules

## Product and UI
- Home Assistant owns automation conditions and follow-up actions. Studio schedules independent measurement Actions for explicitly configured virtual Devices; it does not execute workflows. Each Device may have multiple sensors backed by different Actions and explicitly configured Execute functions that invoke one selected Action. Function calls update only that Action’s sensors on the Device and do not reset its measurement schedule.
- The Devices table is the main page; Actions remain a separate page. Create action opens a right-hand side panel with name and action type. Editing opens a separate configuration page with Input, type settings and Output. No workflow diagram, mappings, templates or activity chains.
- New Echo instances come from the separate ha-ai-studio-actions-core Git repository. The original Echo runtime under `ha_ai_studio/app/action_types/legacy_echo/` is compatibility-only for saved instances and is not offered for creation. Trusted Git action types use validated manifests, a schema editor and a separate per-instance venv runner. Local development sources follow the Local Action development rules below.
- Keep the Home Assistant visual style: HA blue, compact navigation, plain cards and clear textual states. All UI, errors, protocol diagnostics and documentation must be English.
- Use only the embedded Codex browser (IAB) for interactive and visual testing; reuse the existing tab. No external Playwright, Chrome or Safari.

## MQTT and state
- Local operation uses the same real MQTT behavior as the HA app; do not show a DEV banner. In HA, additionally discover the supplied MQTT broker automatically at startup.
- Shared brokers live in Settings. Offer an HA default only after successful Supervisor discovery. Optional private .env adds a read-only development broker; no invented defaults or simulation fallback.
- Input selects broker/request topic; Output defaults to Same as input or selects another broker/result topic. Connect only active Actions; reject route cycles, including between active Actions. Ignore result envelopes on request topics as defense against external bridges/aliases.
- Use MQTT v5 with Retain As Published to reject both live and replayed retained requests. Requests/results use QoS 1 and retain off. No internal activity MQTT.
- Keep drafts separate from active configuration. UI tests must never publish to MQTT or modify production job state.
- Persist request digest and configuration before execution. Deduplicate per action/ID, reject changed payloads, do not rerun interrupted work. Persist execution and delivery separately; do not promise exactly-once delivery.
- Use the HA AI Studio naming consistently. Preserve private data and broker credentials; future identity changes require an explicit migration. Old agents/workflows are unsupported and inactive; never automatically convert them to Echo.
- Never expose broker credentials in browser responses or logs. Do not include private data, images, addresses or secrets in public files/packages.

## Scope and verification
- Work only in this development copy; do not change live HA or ESPHome, publish or deploy without an explicit request.
- Isolate every development worktree's Docker environment. Use unique image tags, Compose project/container/network names, host ports and data directories. Never overwrite or restart another worktree's containers or reuse its private state. A unique Compose project name alone is insufficient: this repository also hard-codes an image tag and host ports, so override those explicitly.
- Before starting or testing, inspect container image IDs and mount paths and verify that the image contains the current worktree's code. Pin the worktree-specific image for the entire test run; concurrent builds must not silently replace the tested image. Test scripts that default to `ha-ai-studio:dev` must be directed to the isolated image.
- Start/rebuild locally with `docker compose up -d --build` using worktree-specific overrides. `http://localhost:8099` is the default only when it is exclusively owned by this worktree; otherwise use and report a dedicated localhost port. Dev uses real configured brokers; UI tests use disposable storage, synthetic Actions and blocked MQTT/HA access; MQTT tests use isolated brokers. Do not resolve port conflicts by stopping another worktree's services.
- Run `./scripts/test.sh` and `./scripts/test-mqtt.sh`. Check all JSON values, ID conflicts, deduplication, restart, retained requests, delivery failures, disabled subscriptions, legacy preservation, secrets and cycle protection.
- In IAB check creation, editing, draft test, activation, deactivation, deletion, desktop/tablet and Ingress subpaths including plugin editor imports.
- Only amd64 is supported. Live HA authentication and production TLS need separate validation.

## Optional Home Assistant discovery
- Central Discovery is explicitly enabled on one selected broker, independent of Action routes; never publish discovery automatically from Input connections.
- Advertise only explicitly published virtual Devices, their sensors and configured function buttons (all functions follow the Device publication setting; no per-function opt-in), never Actions or a Studio diagnostic device. Preserve Device identities and sensor unique IDs. Durably clean up exact owned legacy Action/Studio discovery topics; disabled Devices remain diagnostic states.
- Persist exact owned topics before publication. Empty retained cleanup must be durable, scoped to owned topics, and retried before broker/prefix migration completes.
- Keep private broker connection snapshots for pending cleanup; protect referenced broker deletion/endpoint edits. Never expose snapshots or payloads in diagnostics or browser responses.
- Reject Action routes overlapping discovery/configuration/state/HA-birth topics, including cached retry destinations. Publish request/result messages with retain off.
- Test birth, real reconnect, clean offline, hard-kill LWT, A/B/C isolation, interrupted cleanup/restart/recovery, persistence failures and UI-test isolation. Distinguish isolated MQTT validation from actual HA integration testing.

- Side panels use a shared 640px width capped at the viewport width and share a contrasting 64px header aligned with the blue app header. Group connection identity/status in one card with a traffic-light indicator plus explicit text; logs use neon green on black, fill remaining height and scroll without wrapping.

- Side panels have a fixed footer for primary and confirmation buttons. Keep the header and footer visible while only the body scrolls; associate footer submit buttons with their forms.

## Local Action development and custom editors
- Explicitly configured local sources are supported on the development server under `HA_AI_STUDIO_LOCAL_ACTIONS`; Compose mounts `LOCAL_ACTIONS_DIR` read-only. They use validated immutable snapshots and the same per-instance runner as Git sources.
- Reload local sources only for inactive, idle instances. Preserve identities, settings and request history; retain historical snapshots. Do not silently follow working-directory edits during execution.
- Trusted Actions may declare `editor: "ui/editor.js"`. Custom configuration editors edit draft settings inside the Action page; Studio still owns saving, schema validation, Input, Output and activation. Serve only UI assets from a bound snapshot; preserve relative imports under Ingress. Editors are trusted browser code, not a sandbox.
