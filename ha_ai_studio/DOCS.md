# HA AI Studio — configured Actions

Home Assistant controls triggers, schedules, conditions and follow-up actions. Studio runs one configured Action per request. Echo is supplied by the separate `ha-ai-studio-actions-core` Git repository. Existing bundled Echo instances keep their compatibility runtime and saved state. There is no AI model or workflow builder in this release.

## Setup

1. Open **Settings** and add a broker. A Home Assistant broker appears as default only when Supervisor discovery succeeds. Local development can use these manual brokers or an optional `.env` broker.
2. In **Settings → Action repositories**, add the public HTTPS Git clone URL for `ha-ai-studio-actions-core` and a branch, tag or commit. Wait for the repository status to become **Ready**.
3. Choose **Create action**, enter a name and select **Echo**. Wait for its Python environment to become **Ready** before activation. Echo needs no third-party dependencies.
4. In **Input**, choose a broker and concrete request topic. In **Output**, keep **Same as input** or select another broker and set a different result topic.
5. In **General**, set the name and **Active** checkbox. **Save** persists both settings and activation state together. Clearing Active and saving closes the subscription and discards queued messages.
6. Open **Message schemas** for the input and output contracts. Use **Test in Home Assistant** for a copyable MQTT test automation.
7. Send requests from Home Assistant; open **Live activity** from the Actions table to follow incoming requests, processing and outgoing responses.

Repository refresh changes the catalog for new instances only; existing instances stay pinned to their original commit. Local sources and private Git credentials are unsupported. Git action draft preview is unavailable. Only install trusted repositories: a venv isolates dependencies, not filesystem or network access.

Connections require MQTT v5. The subscriber uses Retain As Published so both live retained requests and retained replay are ignored. The broker connection test checks connection/authentication without publishing; it does not prove all topic ACLs. Active Actions need subscribe permission on their request topic and publish permission for results and diagnostics. The app keeps existing diagnostic discovery IDs on the input broker.

Input topics cannot be shared by active Actions on the same configured endpoint. Direct and multi-Action routing cycles are rejected, including when editing broker endpoints. Canonical host/port comparison cannot identify every DNS alias or external bridge; top-level result envelopes containing `status` are therefore ignored at the request boundary as an additional loop safeguard.

## Echo contract

Publish UTF-8 JSON with QoS 1 and retain disabled:

```json
{"request_id":"test-001","data":{"message":"Hello","value":42}}
```

The result is:

```json
{"request_id":"test-001","status":"success","data":{"message":"Hello","value":42}}
```

`data` is required and accepts every finite JSON value: null, false, zero, strings, arrays and objects. Echo preserves values semantically; whitespace, numeric spelling and object key order are not preserved. The request must have exactly `request_id` and `data`; duplicate JSON keys and non-finite/overflowing numbers are rejected. `request_id` must have 1–128 characters, contain a non-whitespace character and no control characters. Maximum request size: 16,384 UTF-8 bytes. Packets over 1 MiB are dropped without a response as a transport guard; packets between the request limit and transport guard receive `message_too_large` when parseable.

Errors use the same envelope:

```json
{"request_id":"test-001","status":"error","error":{"code":"request_id_conflict","message":"This request_id was already used for different data."}}
```

Codes: `invalid_request`, `invalid_request_id`, `message_too_large`, `request_id_conflict`, `interrupted`, `execution_failed`. Invalid JSON produces `request_id: null`; a missing or invalid ID also produces null. A valid ID is retained for parseable validation errors. No raw payload or credentials are included in errors. Inactive Actions do not subscribe or reply; reply envelopes arriving on request topics are ignored.

## Delivery and persistence

The most recent 256 production IDs per Action are retained across restart. Identical parsed content with the same ID replays the cached result. Different content conflicts. IDs are isolated between Actions. Do not reuse expired IDs: after cache eviction they can execute again.

Intent, digest and active configuration are stored before execution. Incomplete intents return `interrupted` after restart and are never automatically re-executed. To intentionally execute again, use a new ID. Execution status (`success`, `error`, `interrupted`) is separate from delivery (`not_sent`, `pending`, `published`, `failed`, `unknown`). A crash during delivery leaves its outcome unknown. Broker acknowledgement does not mean Home Assistant processed a result. MQTT results can arrive more than once; no exactly-once delivery is promised.

There is no automatic retry queue. If delivery fails or a result is missing, resend the identical request on the active request topic while its ID is cached. The result uses the original saved output broker ID/topic. Broker deletion is blocked while drafts, active configurations or cached jobs reference it. Editing a broker changes the endpoint behind that ID, including retries. One request executes at a time per Action; the volatile queue holds 16 requests. Overflow is dropped; clients must time out and retry with the same ID. Action execution timeout is 30 seconds.

Manual credentials are atomically stored with private filesystem permissions. Empty credential fields preserve saved values; **Clear saved credentials** removes them. Browser responses disclose only whether a credential exists. Credentials are not encrypted at rest; protect app data, backups and Docker administrator access. Supervisor and `.env` credentials stay in memory. No personal HA API token is required.

## Home Assistant automation example

These are documentation examples for the automation UI's **Edit in YAML** mode, not installed automations. Use the same broker HA's MQTT integration uses, and configure the Echo Action with Input `ha-ai-studio/example/echo/request` and Output `ha-ai-studio/example/echo/result`. If Output uses a different broker, HA needs an explicitly configured bridge/consumer for that result route; its MQTT integration does not automatically follow Studio's broker selection.

For correlation, create a **Text helper** in Settings → Devices & services → Helpers with entity ID `input_text.ha_ai_studio_echo_pending`, maximum length 128, and empty initial contents. The example permits only one outstanding request. It records the ID before publishing, and the separate receiver is already subscribed, avoiding a publish-then-wait race.

Request automation (fire the custom event `ha_ai_studio_echo_requested` to start; replace it with your own HA trigger later):

```yaml
alias: HA AI Studio - request Echo
mode: single
triggers:
  - trigger: event
    event_type: ha_ai_studio_echo_requested
conditions:
  - condition: state
    entity_id: input_text.ha_ai_studio_echo_pending
    state: ""
actions:
  - variables:
      request_id: "{{ trigger.event.context.id }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.ha_ai_studio_echo_pending
    data:
      value: "{{ request_id }}"
  - action: mqtt.publish
    data:
      topic: ha-ai-studio/example/echo/request
      qos: 1
      retain: false
      payload: >-
        {{ {'request_id': request_id, 'data': {'message': 'Hello', 'value': 42}} | to_json }}
```

Correlated result automation (handles success and errors, ignores unrelated IDs; notification updates with the same ID are idempotent):

```yaml
alias: HA AI Studio - receive Echo
mode: queued
max: 10
triggers:
  - trigger: mqtt
    topic: ha-ai-studio/example/echo/result
    qos: 1
actions:
  - variables:
      result: "{{ trigger.payload | from_json({}) }}"
  - condition: template
    value_template: >-
      {{ result is mapping
         and result.get('status') in ['success', 'error']
         and result.get('request_id') is string
         and result.get('request_id') != ''
         and result.get('request_id') == states('input_text.ha_ai_studio_echo_pending') }}
  - action: persistent_notification.create
    data:
      notification_id: "{{ 'ha-ai-studio-' ~ result['request_id'] }}"
      title: "HA AI Studio result"
      message: "{{ result | to_json }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.ha_ai_studio_echo_pending
    data:
      value: ""
```

The template condition is needed to compare the incoming correlation ID with the stored ID. The notification includes error information as well as successful data. This example has no automatic timeout/retry: a missing result leaves the helper populated and blocks new requests. Retry with that saved ID and identical data; clear it manually only when abandoning that request. HA owns any more advanced timeout policy. The example is syntax/reference reviewed, not tested in a live HA installation.

References: [MQTT publish action](https://www.home-assistant.io/actions/mqtt.publish/), [MQTT trigger](https://www.home-assistant.io/triggers/mqtt/), [MQTT trigger data](https://www.home-assistant.io/docs/automation/trigger-data/#mqtt-triggers).

## Migration and limits

Old Agents/workflows remain **Unsupported · Disabled**. Nothing is converted into Echo. Existing registry paths and identity files are read without automatic conversion, including the `/agents/` protocol path. Historical private records are not executed. The current app slug is `ha_ai_studio`; changing installations or stored identities requires an explicit data and MQTT migration. New Actions require explicit creation and activation.

Delete stops an Action and removes it from the active table; private files and its reserved ID remain. There is no restore button. Up to 32 registered Actions and 32 manual brokers are supported. Only amd64 is supported. Live HA Ingress authentication and production TLS remain to be tested. See `app/action_types/README.md` before adding a future Action type.

## Home Assistant discovery

In **Settings**, edit the broker used by Home Assistant. In its side panel, check **Enable MQTT Discovery**, and choose **Save broker**. New brokers can enable discovery in the same panel. Discovery is off initially and never enabled automatically. Only one broker can provide discovery at a time; enabling it on another broker moves discovery after cleanup. The broker table shows whether discovery is enabled. Discovery settings are also available for read-only Supervisor and environment brokers. **Save broker** saves connection and discovery settings together. If discovery validation fails after the connection was saved, the panel stays open and explicitly reports that partial result so you can correct and retry. This connection is independent of Action Input and Output and can run with no Actions. Use **Refresh status** to check publication or pending cleanup. Advanced topics default to discovery prefix `homeassistant` and HA status topic `homeassistant/status`.

The Studio device exposes its active Action count. Each supported configured Action exposes execution and MQTT delivery diagnostics and links to the Studio device. Disabled Actions remain visible as `disabled`. Execution can be `idle`, `running`, `success`, `error` or `interrupted`; delivery can be `not_sent`, `pending`, `published`, `failed` or `unknown` after an interrupted delivery. `published` means broker acknowledgement, not recipient processing. Very short intermediate states may occur between diagnostic refreshes. Draft tests never change these production diagnostics.

MQTT Discovery creates devices and entities, **not a native HA action or generated input form**. Continue to use `mqtt.publish` and correlate result messages by `request_id`, as in the automation example above. Discovery does not execute Actions.

Names can change without changing stored identifiers. Existing Action status identifiers and the installation identity are reused. New Action identifiers derive from installation namespace and Action ID. Unsupported legacy records are not advertised. A legacy Action sharing the installation device identity remains on that device rather than creating a duplicate.

On deletion, disabling discovery, broker switch or prefix change, Studio clears only its recorded configuration, diagnostic and availability topics. Historical HA statistics and unrelated topics are not deleted. Keep the old broker reachable: pending cleanup persists across restarts and completes before new announcements. Brokers needed by cleanup cannot be deleted or have their endpoint changed. Disable discovery, clear its broker selection, save, and wait for cleanup before deleting a broker. Credentials needed for cleanup are retained privately in the broker registry, including Supervisor connections; they are never returned to the browser. Rotated credentials can be updated on a matching manual broker endpoint.

For upgrades from the earlier automatic Input-broker discovery, known status topics are recorded for reconciliation on the first explicit discovery save. Existing identity files must be retained. Discovery cannot infer historical broker addresses or topics that older versions did not record; keep the original broker connection available during migration. No wildcard or guessed cleanup is performed.

Birth/Last Will marks Studio online/offline. Startup, reconnect and HA's online message republish configurations and current states. Discovery/diagnostics are retained; requests/results remain non-retained. Official protocol references: [MQTT discovery and birth](https://www.home-assistant.io/integrations/mqtt/) and [MQTT sensor device/availability fields](https://www.home-assistant.io/integrations/sensor.mqtt/).

### Broker connection test

**Settings → broker → Test** opens a right-hand panel immediately. After the check finishes it shows timed diagnostic steps for host resolution, TCP/TLS setup and MQTT v5 connection acknowledgement. Errors are mapped to safe English messages; credentials and raw library/server error text are never displayed. The test uses saved settings from the app container, closes its temporary connection, and does not publish or subscribe. It does not verify topic ACLs. A refused TCP connection occurs before authentication: check the listener, port and firewall before changing credentials.

### Test from Home Assistant

Open an Action and choose **Test in Home Assistant**, then **Copy YAML**.

1. In Home Assistant, open Settings → Automations & scenes and create a new automation.
2. Select ⋮ → Edit in YAML and replace the content with the copied YAML.
3. Save the automation and keep it enabled.
4. Select ⋮ → Run actions. Open the notification bell to read the result.

Manual execution publishes a real MQTT request with QoS 1, retain off and a new request ID. The enabled MQTT trigger already listens for results, including immediate replies; parallel mode allows a reply while the sender is still running. Every response on the Action's Result topic creates a notification. This simple test does not filter responses by request ID and does not report timeouts.

Active Actions use their active configuration; inactive Actions show a draft example and must be saved and activated before testing. Home Assistant must use the Input broker. If Output uses another broker, explicitly bridge the Result topic to HA's MQTT broker. Opening or copying the example does not publish messages.

### Stable installation identity

On first start, Studio creates a random installation namespace and persists its `instance_id` in `/data/identity.json`. Restarts and upgrades using the same data directory keep this ID. Existing installations adopt their current namespace without changing MQTT topics or Discovery unique IDs.

Discovery device names include the complete instance ID: `HA AI Studio · studio_<id>`. Action devices include both the instance ID and action name, and link to their parent Studio device. Independently initialized data directories produce distinct MQTT namespaces, entity unique IDs and device identifiers, even when actions have identical names. The Discovery status API also returns `instance_id`.

Use a separate persistent data directory for each installation. Restoring or copying an entire data directory preserves the identity; a clone is not a new installation and must not run alongside its original against the same broker. For a new instance, start with an empty data directory. Discovery remains opt-in.

### Action editor

The **General** section contains Name and Active. **Save** persists configuration and the selected Active state together. Checking Active validates the broker routes and activates the saved configuration. Clearing Active and saving stops the subscription; failed saves leave the prior configuration/state intact. Unsaved checkbox changes do not affect MQTT.

**AI Activity** displays the editor supplied by the selected plugin. **Message schemas** opens a side panel from the editor toolbar. The local Test action UI is no longer shown; use **Test in Home Assistant** for the MQTT test automation. The isolated backend test endpoint remains available for automated checks.

## Live activity

Open the Live activity icon in the Actions table. The view refreshes every second and shows incoming requests, execution stages, outgoing response payloads and broker delivery acknowledgements. Pause freezes only the display; the Action keeps running. Scroll up to stop following the tail. Each Action keeps at most 200 events in memory, cleared on app restart, with payloads capped at 20,000 characters. Payloads can contain private application data; they are only exposed through the existing local/Ingress UI boundary, never sent to Discovery or written to application logs. Broker credentials are not included. Retained requests rejected by the MQTT transport do not appear in this view.

During shutdown, in-flight blocking MQTT calls finish before their clients are closed. A cancelled output operation is recorded as `unknown`, so it is never mistaken for a confirmed failure or success. Retry the identical request ID to resend its cached result.

## Derived Device sensors: rate of change

In a Device, add a sensor and select **Sensor type → Rate of change**. Select an Action-backed sensor in the same Device as its source, enter a positive conversion factor, choose a time base (second, minute or hour), and set the output unit. Derived sources are not supported. The source Action executes only once per measurement, regardless of how many sensors use it.

For a water meter in m³, select factor **1000**, time base **Per minute**, and unit **L/min**. The result is `(new value − previous value) × factor ÷ elapsed time in the selected time base`. A rise from 123.450 to 123.465 m³ over five minutes produces 3 L/min. This is the average over the measurement interval, not instantaneous flow or evidence of uninterrupted consumption. Alarm conditions and notifications belong in Home Assistant.

Source values and measurement times survive restarts. Actions can supply `measured_at` as finite Unix seconds on a reading; otherwise Studio uses the Action completion time. Supplied timestamps must not be in the future, and accepted source timestamps must strictly increase. First readings, invalid or textual sources, failed Actions and non-finite calculations make the rate unavailable; they never imply zero flow. Equal valid counter readings do produce zero. Cumulative sources reject decreasing values; generic non-cumulative numeric sources may produce signed rates. Invalid source readings retain the last valid baseline, so recovery averages over the longer interval.

Rate sensors have their own MQTT availability in addition to Studio availability. Studio retains their last successful values and timestamps for diagnostics, but shows **Unavailable** while the result is invalid or the Device is disabled. Changing a source's Action or reading binding resets its value and dependent rate values. Use a new source sensor ID for a replacement counter. Activated/published rate definitions (source, factor and time base) are bound to their sensor identity; use a new sensor ID to change them without mixing Home Assistant history. Draft edits do not affect active measurements.
