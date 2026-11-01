# Action plugins

New action types come from configured Git repositories. Echo is maintained in the separate `ha-ai-studio-actions-core` repository under `actions/echo/`. The `legacy_echo/` directory and `TYPES` registry are compatibility-only for previously saved instances; they are excluded from the creation catalog and creation API. Existing IDs, routes, jobs and discovery identities remain unchanged. No automatic migration is performed. No workflow graph is supported.

A plugin provides:

- `metadata`: stable `type`, English `name` and `description`, `input_schema`, `output_schema`, and an example request.
- `validate(settings)`: validate type-specific configuration without side effects; raise a safe English `Invalid` error.
- `validate_input(message)`: validate the request envelope after common request-ID, JSON and size checks.
- `async execute(data, settings)`: return independent JSON data. Do not mutate inputs or access broker credentials. The runtime limits execution to 30 seconds.
- `editor.js`: export `render(host, settings, context)`; render safe DOM content and return a getter for settings. Call `context.changed()` on edits. Do not silently materialize defaults when merely viewing configuration.

Common infrastructure owns brokers, lifecycle, envelope validation, durable intent, deduplication, execution and delivery state. Production dispatch calls a single plugin directly; no internal MQTT activity messages exist. Input and output use configured external MQTT routes.

The UI test endpoint runs the submitted draft without connecting MQTT, writing production jobs or publishing results. Future plugins with side effects require an explicit safe preview design before they may be offered in this endpoint. Plugins are trusted code, not a sandbox. Adding a bundled type requires rebuilding and testing the app.

## Trusted Git action repositories

The public `https://github.com/ha-ai-studio/ha-ai-studio-actions-core.git` source
is added automatically at startup with ref `main`, including on existing installations.
An existing entry for this repository keeps its ID and selected ref. Only missing
or failed default catalogs are refreshed at startup; no action instance is created
and no repository code is installed or executed. System sources are marked System
and cannot be deleted. User sources can be deleted only when no existing action
(including inactive or unconfigured instances) references them and no refresh is
running. Deletion removes the local catalog; the remote Git repository is untouched.
Historical files and snapshots referenced by deleted actions remain preserved.

Settings → Action repositories accepts public HTTPS Git repositories and a branch,
tag or commit. Refresh reads metadata only. Existing instances stay pinned to their
original commit; refreshing changes the catalog for newly created instances only.
Private Git credentials and in-place Git version upgrades are not yet supported. Local development sources support explicit reload as described below.

Repository layout:

```text
actions/example/action.json
actions/example/action.py
actions/example/requirements.lock
```

Example `action.json` (Python must match the app runtime, currently 3.12):

```json
{
  "id": "example",
  "name": "Example",
  "description": "Return JSON data unchanged.",
  "version": "1.0.0",
  "python": "3.12",
  "entrypoint": "action.py",
  "settings_schema": {"type": "object", "additionalProperties": false},
  "input_schema": {},
  "output_schema": {},
  "example": {"request_id": "example-001", "data": {"message": "Hello"}}
}
```

`input_schema` and `output_schema` validate the JSON **data**, not the transport
envelope. Schemas use JSON Schema Draft 2020-12; references are unsupported.
If provided, `example` must be a valid request envelope matching the input schema
and request size/ID rules. A default example is offered only when it validates;
otherwise Test in Home Assistant is unavailable until a valid example is supplied.
Installation checks that `execute` accepts the positional arguments `data, settings`
without calling the function.
The settings editor supports primitive properties and falls back to a JSON editor
for complex schemas. An optional trusted custom editor can replace the schema editor; see below.

`action.py` exports `execute(data, settings)`, either sync or async, returning finite
JSON. Sibling Python modules are supported. Stdout and stderr are discarded; the
application shows safe lifecycle diagnostics rather than arbitrary plugin output.
Execution is limited to 30 seconds and 65536 bytes of runner output.

`requirements.lock` can be empty for stdlib-only actions. Otherwise each requirement
must have an exact version and SHA256 hashes, including all transitive dependencies:

```text
package-name==1.2.3 --hash=sha256:<64 hexadecimal characters>
```

Standard backslash continuations are supported. Only registry wheels are installed;
source builds, direct URLs, editable/local requirements and installer options are
unsupported. No system packages are installed dynamically.

Creating an instance queues installation into its own persistent venv. Only Ready
environments can execute or activate. Rebuild requires an inactive, idle instance.
Interrupted installations fail closed and require an explicit rebuild. Environments
are checked for interpreter compatibility on restart. Failed installations expose
only fixed English diagnostics. Raw subprocess output and inherited app credentials
are never exposed. The immutable plugin binding is persisted with each job before
execution; execution and MQTT delivery retain their separate durable states.

**Trust boundary:** a venv isolates dependencies, not files, processes or networking.
Repository code runs with the app's OS permissions and must be trusted. A separate
process and filtered environment do not provide a security sandbox. Git and local Actions have
no draft preview until a safe preview contract is implemented. Use isolated brokers
and synthetic data for integration testing. Existing bundled Echo instances retain their compatibility runtime. New Echo instances use the Git runner.

Git schema validation runs in separate one-shot processes (including manifest and example checks). Each worker has a 1-second CPU limit, 256 MiB address-space growth limit above the initial interpreter mappings, and a 3-second wall deadline including queueing. At most two validators run concurrently. Limits fail closed with a sanitized error; cancellation kills and reaps the process group. Catalog rendering reuses examples validated during import and never evaluates schemas. These limits are resource isolation, not a sandbox for untrusted repositories.

## Local development sources

In development mode (`HA_AI_STUDIO_DEV=1`), explicitly set `HA_AI_STUDIO_LOCAL_ACTIONS` to a server directory. The Compose development server mounts `LOCAL_ACTIONS_DIR` read-only at `/local-actions`; its default is the sibling `ha-ai-studio-actions-ai-on-the-edge-meters` example directory. Add a **Local directory** source in Settings with a relative path (`.` for the mounted root). A source may contain `actions/<id>/` directories or be one action directory. Absolute paths, traversal and symlinks are rejected. Dotfiles, dependency caches and common private key files are excluded; keep all other private data out of action sources too. Sources are limited to 2048 files, 5 MiB per file and 32 MiB total.

Refresh copies and validates a fixed snapshot without executing code. New instances use that snapshot. **Reload from source** on an inactive, idle local instance refreshes its source, validates saved draft settings against the new schema, pins the new snapshot and rebuilds its environment. Save or discard unsaved browser edits first. Identities, routes, configuration and deduplication history remain unchanged. Historical snapshots stay preserved. Installation failure blocks execution until recovery; interrupted installation requires rebuilding. Git instances remain pinned and have no in-place reload.

## Trusted custom configuration editors

Add `"editor": "ui/editor.js"` to the manifest. It must be an ES module exporting `render(host, settings, context)`. Return a synchronous settings getter, or `{read, dispose}` where `dispose()` removes any external listeners/resources on unmount. Call `context.changed()` after an edit. Do not mutate the supplied settings or silently populate defaults on opening. Studio passes a detached draft copy and saves/validates the getter's JSON result through its existing schema validation. A missing editor declaration uses the schema editor. A broken declared editor blocks saving with an English diagnostic.

Use relative imports (`./fields.js`) and relative asset URLs (`new URL('./style.css', import.meta.url)`) so modules work under Ingress. Assets must be in `ui/`; supported extensions are `.js`, `.css`, `.svg`, `.png`, `.jpg`, `.webp`, `.woff2`. Python, lockfiles, dotfiles and files outside `ui/` are never served by the plugin asset endpoint. Only snapshots currently bound to an instance serve UI assets. A reload changes the snapshot URL, avoiding the browser module cache. UI code must follow the app CSP (no inline scripts/styles, eval or external CDN).

Editors execute trusted JavaScript with Studio's browser privileges. This is not a security sandbox. They should only implement configuration controls; Input, Output, activation and saving remain Studio-owned. Never put secrets or private data in `ui/`. Custom editors do not enable draft execution for Git/local actions; use isolated MQTT integration tests.

Schema evolution: reload currently rejects a source whose settings schema does not accept the saved draft. When adding incompatible required settings, keep the schema temporarily compatible or create a new inactive instance from the refreshed catalog. There is no automatic settings migration.

## Built GitHub releases

Settings → Action repositories → **GitHub Release** accepts a public GitHub
repository URL and `latest` or an explicit stable tag. Refresh resolves `latest`
through GitHub's latest-release API; drafts and prereleases are rejected. The
release must attach `ha-ai-studio-actions.zip` and
`ha-ai-studio-actions.zip.sha256` (one line: lowercase SHA256, two spaces, filename).
GitHub's automatically generated source archives are not used.

The ZIP contains `actions/<id>/` with the same manifest/runtime contract as Git
sources, including already-built `ui/` assets. Downloads and extraction are bounded
(32 MiB total, 5 MiB per file, 2048 entries); absolute/traversing paths, dotfiles,
duplicate paths, encrypted entries and symlinks are rejected. The checksum detects
corruption, not an untrusted publisher. Only install trusted repositories.

Studio does not run npm or build scripts. It stores the resolved release tag, ID
and package SHA256 in each immutable binding. Refresh changes only the creation
catalog; existing Actions keep their version and snapshots across refresh and
restart. There is no automatic conversion of existing Git/local sources or in-place
release upgrade. Failed refreshes preserve the previous catalog. GitHub network
access and unauthenticated API rate limits apply; private releases are unsupported.

## Sensor history for trusted Actions

Actions declaring `home_assistant` can call
`context['home_assistant'].history('sensor.example', hours=24)` during execution.
The entity must be present in saved settings. The server owns authentication and
limits queries to 1–168 whole hours and 10000 records. The result contains
`entity_id`, UTC `start`/`end`, and `readings` with only `state`, `timestamp`, `unit`
and `state_class`. The timestamp is HA's `last_updated`. Arbitrary attributes and
context are stripped. The client response has a 4 MiB limit; HA responses retain
the server's 8 MiB limit. Missing history is not a zero reading.

A ready-to-import development Action is provided in
`actions/water-leak-detection` in the separate
`ha-ai-studio-actions-ai-on-the-edge-meters` repository. See its Action README
for setup, sensor bindings and assessment limitations.
It is an explicit local source, not an automatically installed bundled type.

String settings can declare `"x-ha-entity-domains": ["sensor"]` to offer the
shared Home Assistant entity search beside the field. Supported domains are
`camera`, `light`, `switch`, and `sensor`. Selection changes only the draft value;
Studio still owns saving and schema validation. The Action must also declare
`home_assistant` to use the selected entity during execution.
