# Frontend

HA AI Studio uses React, strict TypeScript and SCSS. The production application is a single Python container: the Dockerfile builds the frontend in an isolated Node stage and copies only `dist` into the runtime. Node, npm and source maps are not needed at runtime. No CDN or separate frontend service is required.

## Structure

- `src/design-system/`: shared accessible buttons, inputs, cards, tables, notices, status summaries, logs, JSON blocks and modal side panels.
- `src/styles/_tokens.scss`: semantic colors, spacing scale, typography, radii, shadows, panel width and header height. Dark-mode values live here too.
- `src/styles/_components.scss`: shared component styling; `_base.scss` contains document defaults and `_app.scss` contains application-specific layout.
- `src/features/`: Actions, configuration, Settings and live activity; panels own their form state and operation errors.
- `src/lib/`: typed backend contracts, relative URL construction, API requests and cancellable polling.

Compose feature pages from design-system components. Add a semantic token before introducing a reusable styling value. Keep business logic out of generic controls. Side panels share a 640px width capped at the viewport, a 64px header, a scrolling body and a fixed footer. Submit buttons in the footer must reference their form by ID. Logs preserve lines and scroll horizontally.

React owns UI state and rendering. Trusted custom configuration editors and the compatibility editor use a managed DOM island, with module imports resolved relative to the document's Ingress path. Actions without a custom editor use the React schema editor. Custom editor modules may provide a cleanup callback and always save through Studio schema validation. Optional numeric settings preserve absence separately from zero; arbitrary settings JSON can contain any JSON value. The backend remains authoritative for validation, activation and MQTT state.

## Development and validation

From the repository root:

```sh
docker compose up -d --build
./scripts/test.sh
./scripts/test-mqtt.sh
./scripts/test-ui.sh
```

Open `http://localhost:8099`. Docker builds run the frontend regression tests and TypeScript checks before producing the bundle, so the existing container CI checks cover the frontend too. `test-ui.sh` runs the same React regression suite locally with Node 22.21 or newer. Fetch is mocked in these tests; they do not contact MQTT or change development/production data.

For optional frontend editing, run `npm ci` then `npm run dev` in this directory. The Vite development server proxies API and compatibility-editor requests to the existing local backend. Production still uses the single container above. `npm run format` formats the TypeScript and SCSS sources.

To check Ingress routing against the real local backend:

```sh
docker compose --profile ingress up -d ingress
```

Open `http://localhost:8100/api/hassio_ingress/dev-check/`. This proxy checks path handling only; it does not emulate Home Assistant authentication.

For mutation and visual checks, run the synthetic UI fixture in the built container, using a disposable container and no data mounts:

```sh
docker run --rm --name ha-react-ui-fixture --platform linux/amd64 \
  -p 127.0.0.1:8101:8101 -e PYTHONPATH=/app \
  -v "$PWD/tests:/tests:ro" ha-ai-studio:dev python /tests/ui_fixture.py
```

Use the embedded Codex browser at `http://localhost:8101/api/hassio_ingress/ui-check/`. This test-only server serves the real built assets and compatibility editor, with synthetic API responses and disposable memory state. It never starts MQTT, repositories or the backend lifecycle. Unknown API endpoints fail closed. It is excluded from published app packages. Verify creation, editing, dirty navigation, activation/deactivation, deletion, compatibility draft preview and desktop/tablet layouts here. Actual backend and isolated MQTT integration are verified by the separate suites above.

The public source archive uses the explicit `FRONTEND_FILES` allowlist in `scripts/package.py`. Register new build inputs there; do not package `node_modules`, captures, local configuration or private data. Runtime dependency notices are in `../THIRD_PARTY.md`.

To also verify the meter example's real custom editor modules in the synthetic fixture, add `-v "$PWD/../ha-ai-studio-actions-ai-on-the-edge-meters:/fixture-meter:ro"` to the fixture container command. This enables the local-source form and a synthetic meter Action type. The fixture only reads public example UI files; it never starts the real Action runner or connects to MQTT.

### Virtual Device browser checks

Start the disposable fixture with `docker compose -p studio-device-preview -f tests/compose.ui.yaml up -d --build` from the repository root. Use only the embedded browser at `http://localhost:8101`; the Ingress path proxy is `http://localhost:8102/api/hassio_ingress/dev-check/`. The fixture contains two synthetic measurement Actions, a custom editor with a relative JavaScript import, and a compatibility Echo for isolated draft preview. It never mounts development data or HA credentials, never starts Action MQTT subscriptions or Discovery, and rejects any attempted MQTT connection. Device activation/Read now affect synthetic fixture state only. Restarting the fixture discards its data.

Check name-only device creation, separate Action assignments for multiple sensors, unsaved edits versus saved configuration, the Active checkbox and Save, Read now, disabling via Save, deletion, and desktop/tablet layout. Verify plugin editor imports under the Ingress prefix. Broker/discovery tests are separate real-MQTT tests on disposable brokers; these checks do not validate live HA authentication or production TLS.
