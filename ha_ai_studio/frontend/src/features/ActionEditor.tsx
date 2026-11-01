import { useEffect, useRef, useState } from "react";
import {
  Button,
  Card,
  Checkbox,
  Input,
  IconButton,
  Notice,
  Select,
} from "../design-system";
import { api, errorText } from "../lib/api";
import { useOperation, usePoll } from "../lib/hooks";
import type {
  ActionState,
  Brokers,
  Configuration,
  Json,
  Route,
} from "../lib/types";
import { DraftTest, ExamplePanel, SchemasPanel } from "./ActionPanels";
import { LegacyEditor, readSettings, SchemaEditor } from "./SchemaEditor";
function BrokerSelect({
  label,
  data,
  value,
  same,
  onChange,
}: {
  label: string;
  data: Brokers;
  value: string | null;
  same?: boolean;
  onChange: (id: string | null) => void;
}) {
  return (
    <Select
      label={label}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
    >
      <option value="">{same ? "Same as input" : "Select a broker"}</option>
      {data.brokers.map((b) => (
        <option key={b.id} value={b.id}>
          {b.name}
          {b.id === data.default_id ? " · HA default" : ""}
        </option>
      ))}
      {value && !data.brokers.some((b) => b.id === value) && (
        <option value={value}>Unavailable broker</option>
      )}
    </Select>
  );
}
export function ActionEditor({
  id,
  back,
  onDirty,
  onBusy,
}: {
  id: string;
  back: () => void;
  onDirty: (value: boolean) => void;
  onBusy: (value: boolean) => void;
}) {
  const [state, setState] = useState<ActionState>();
  const [brokers, setBrokers] = useState<Brokers>();
  const [draft, setDraft] = useState<Configuration>();
  const [staleVersion, setStaleVersion] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [loadingError, setLoadingError] = useState("");
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [raw, setRaw] = useState("");
  const [editorBusy, setEditorBusy] = useState(false);
  const [legacyReady, setLegacyReady] = useState(false);
  const legacyRead = useRef<(() => Json) | null>(null);
  const [panel, setPanel] = useState<"schemas" | "example" | "test" | null>(
    null,
  );
  const [panelConfig, setPanelConfig] = useState<Configuration>();
  const [message, setMessage] = useState("");
  const op = useOperation();
  const version = useRef(0);
  useEffect(() => {
    onDirty(dirty);
    return () => onDirty(false);
  }, [dirty, onDirty]);
  useEffect(() => {
    onBusy(op.busy || editorBusy);
    return () => onBusy(false);
  }, [op.busy, editorBusy, onBusy]);
  function load(value: ActionState) {
    setState(value);
    setStaleVersion(false);
    setSelectedVersion(value.plugin?.snapshot || "");
    if (!value.config || !value.supported) {
      setDirty(false);
      return;
    }
    setLoadingError("");
    legacyRead.current = null;
    setLegacyReady(false);
    setDraft(structuredClone(value.config.draft));
    setEnabled(value.enabled);
    setValues({});
    setRaw(JSON.stringify(value.config.draft.settings, null, 2));
    setDirty(false);
  }
  useEffect(() => {
    const abort = new AbortController();
    void Promise.all([
      api("action", undefined, id, abort.signal),
      api("brokers", undefined, undefined, abort.signal),
    ])
      .then(([value, data]) => {
        if (!abort.signal.aborted) {
          load(value);
          setBrokers(data);
        }
      })
      .catch((error) => {
        if (!abort.signal.aborted) setLoadingError(errorText(error));
      });
    return () => abort.abort();
  }, [id]);
  usePoll(async (signal) => {
    const generation = version.current;
    const value = await api("action", undefined, id, signal);
    if (
      !signal.aborted &&
      generation === version.current &&
      value.plugin?.snapshot !== state?.plugin?.snapshot
    ) {
      if (dirty || editorBusy) setStaleVersion(true);
      else load(value);
      return;
    }
    if (!signal.aborted && generation === version.current)
      setState((previous) =>
        previous
          ? {
              ...previous,
              versions: value.versions,
              environment: value.environment,
              issues: value.issues,
              busy: value.busy,
            }
          : previous,
      );
  }, !!state?.plugin?.dynamic && !op.busy);
  function changed() {
    setDirty(true);
    setMessage("");
  }
  function updateRoute(key: "input" | "output", value: Partial<Route>) {
    setDraft((previous) =>
      previous
        ? { ...previous, [key]: { ...previous[key], ...value } }
        : previous,
    );
    changed();
  }
  function configuration(): Configuration {
    if (!draft || !state?.plugin)
      throw new Error("Action settings are not ready.");
    if ((!state.plugin.dynamic || state.plugin.editor) && !legacyRead.current)
      throw new Error("Compatibility editor is not ready.");
    return {
      ...draft,
      settings:
        state.plugin.dynamic && !state.plugin.editor
          ? readSettings(
              state.plugin.settings_schema,
              draft.settings,
              values,
              raw,
            )
          : legacyRead.current!(),
    };
  }
  if (!state || !brokers)
    return (
      <>
        <Button variant="text" onClick={back}>
          ← Actions
        </Button>
        <Notice error={!!loadingError}>
          {loadingError || "Loading action…"}
        </Notice>
      </>
    );
  if (!state.supported || !draft)
    return (
      <section>
        <Button variant="text" onClick={back}>
          ← Actions
        </Button>
        <h1>{state.definition.name}</h1>
        <p role="status">Unsupported legacy action · Disabled</p>
        <Notice>{state.issues.join(" ")}</Notice>
      </section>
    );
  const ready =
    !staleVersion &&
    !editorBusy &&
    ((!!state.plugin?.dynamic && !state.plugin.editor) || legacyReady);
  return (
    <section>
      <Button variant="text" onClick={back} disabled={op.busy}>
        ← Actions
      </Button>
      <div className="heading">
        <h1>{state.definition.name}</h1>
        <div className="actions">
          {state.enabled && (
            <Button
              disabled={op.busy || state.busy}
              onClick={() => {
                version.current++;
                void op.run(async () => {
                  const value = await api("deactivate", {}, id);
                  setState(value);
                  setEnabled(false);
                  setMessage("Action deactivated.");
                });
              }}
            >
              Deactivate
            </Button>
          )}
          {state.plugin?.local && (
            <Button
              disabled={
                op.busy || state.busy || state.enabled || dirty || editorBusy
              }
              title="Save or discard changes and deactivate the action before reloading."
              onClick={() => {
                version.current++;
                void op.run(async () => {
                  load(await api("reload-source", {}, id));
                  setMessage("Local source reloaded.");
                });
              }}
            >
              Reload from source
            </Button>
          )}
          {!state.plugin?.dynamic && state.supported && (
            <Button
              disabled={op.busy || !ready}
              onClick={() =>
                void op.run(async () => {
                  setPanelConfig(configuration());
                  setPanel("test");
                })
              }
            >
              Test draft
            </Button>
          )}
          <Button
            title={
              !state.plugin?.example
                ? "No valid example request is available for this action."
                : ""
            }
            disabled={
              op.busy || !state.supported || !state.plugin?.example || !ready
            }
            onClick={() =>
              void op.run(async () => {
                setPanelConfig(
                  state.enabled && state.config?.active
                    ? state.config.active
                    : configuration(),
                );
                setPanel("example");
              })
            }
          >
            Test in Home Assistant
          </Button>
          <Button
            disabled={op.busy || !state.supported}
            onClick={() => setPanel("schemas")}
          >
            Message schemas
          </Button>
          <Button
            form="action-config"
            type="submit"
            variant="primary"
            disabled={op.busy || !state.supported || !ready}
          >
            Save
          </Button>
        </div>
      </div>
      <p role="status">
        {!state.supported
          ? "Unsupported legacy action · Disabled"
          : dirty
            ? "Unsaved changes"
            : message}
      </p>
      <Notice error>
        {op.error || loadingError || state.issues.join(" ")}
      </Notice>
      {staleVersion && (
        <Notice error>
          The action version changed elsewhere. Reload to use its current
          settings editor.
          <Button
            onClick={() =>
              void op.run(async () => load(await api("action", undefined, id)))
            }
          >
            Discard changes and reload
          </Button>
        </Notice>
      )}
      {state.supported && (
        <form
          id="action-config"
          onSubmit={(e) => {
            e.preventDefault();
            version.current++;
            void op.run(async () => {
              const value = await api(
                "action",
                {
                  config: configuration(),
                  enabled,
                  snapshot: state.plugin?.snapshot,
                },
                id,
              );
              load(value);
              setMessage(
                enabled ? "Action saved. Active." : "Action saved. Inactive.",
              );
            });
          }}
        >
          <fieldset disabled={op.busy || staleVersion}>
            <Card title="General">
              <Input
                label="Name"
                required
                maxLength={80}
                value={draft.name}
                onChange={(e) => {
                  setDraft({ ...draft, name: e.target.value });
                  changed();
                }}
              />
              {state.plugin?.dynamic && (
                <>
                  <Select
                    label="Version"
                    value={selectedVersion}
                    disabled={
                      op.busy ||
                      state.busy ||
                      state.enabled ||
                      dirty ||
                      editorBusy
                    }
                    onChange={(e) => setSelectedVersion(e.target.value)}
                  >
                    {(state.versions || []).map((item) => (
                      <option key={item.snapshot} value={item.snapshot}>
                        {item.version} ·{" "}
                        {item.release?.tag || item.commit.slice(0, 12)}
                        {item.snapshot === state.plugin?.snapshot
                          ? " · Current"
                          : ""}
                      </option>
                    ))}
                  </Select>
                  <p className="small muted">
                    Select a downloaded version to upgrade or downgrade. Save or
                    discard settings first. The action and devices using it must
                    be inactive.
                  </p>
                  <div className="action-version-controls">
                    {state.versions?.find(
                      (item) => item.snapshot === selectedVersion,
                    )?.url && (
                      <a
                        href={
                          state.versions.find(
                            (item) => item.snapshot === selectedVersion,
                          )!.url!
                        }
                        target="_blank"
                        rel="noreferrer"
                      >
                        View release or commit
                      </a>
                    )}
                    <IconButton
                      icon="apply"
                      label="Apply version"
                      title="Apply the selected action version"
                      disabled={
                        !selectedVersion ||
                        selectedVersion === state.plugin.snapshot ||
                        op.busy ||
                        state.busy ||
                        state.enabled ||
                        dirty ||
                        editorBusy
                      }
                      onClick={() => {
                        version.current++;
                        void op.run(async () => {
                          load(
                            await api(
                              "action-version",
                              { snapshot: selectedVersion },
                              id,
                            ),
                          );
                          setMessage(
                            "Version saved. Preparing the Python environment.",
                          );
                        });
                      }}
                    />
                  </div>
                </>
              )}
              <Checkbox
                label="Active"
                checked={enabled}
                disabled={
                  !!state.plugin?.dynamic &&
                  state.environment.status !== "Ready" &&
                  !state.enabled
                }
                onChange={(e) => {
                  setEnabled(e.target.checked);
                  changed();
                }}
              />
            </Card>
            <Card title="Input">
              <BrokerSelect
                label="Broker"
                data={brokers}
                value={draft.input.broker_id}
                onChange={(broker_id) => updateRoute("input", { broker_id })}
              />
              <Input
                label="Request topic"
                required
                value={draft.input.topic}
                onChange={(e) =>
                  updateRoute("input", { topic: e.target.value })
                }
              />
            </Card>
            <Card title="Action settings">
              {state.plugin?.dynamic && !state.plugin.editor ? (
                <SchemaEditor
                  schema={state.plugin.settings_schema}
                  initial={draft.settings}
                  values={values}
                  raw={raw}
                  onValues={(value) => {
                    setValues(value);
                    changed();
                  }}
                  onRaw={(value) => {
                    setRaw(value);
                    changed();
                  }}
                />
              ) : (
                <LegacyEditor
                  type={state.definition.type}
                  editor={state.plugin?.editor}
                  actionId={id}
                  onBusy={setEditorBusy}
                  settings={draft.settings}
                  onReady={(read) => {
                    legacyRead.current = read;
                    setLegacyReady(!!read);
                  }}
                  onError={setLoadingError}
                  onChange={changed}
                />
              )}
            </Card>
            <Card title="Output">
              <BrokerSelect
                label="Broker"
                data={brokers}
                value={draft.output.broker_id}
                same
                onChange={(broker_id) => updateRoute("output", { broker_id })}
              />
              <Input
                label="Result topic"
                required
                value={draft.output.topic}
                onChange={(e) =>
                  updateRoute("output", { topic: e.target.value })
                }
              />
              <p className="small muted">
                Results use QoS 1 with retain off. Match each result by
                request_id.
              </p>
            </Card>
          </fieldset>
        </form>
      )}
      {panel === "schemas" && state.plugin && (
        <SchemasPanel type={state.plugin} close={() => setPanel(null)} />
      )}
      {panel === "example" && panelConfig && (
        <ExamplePanel
          config={panelConfig}
          state={state}
          brokers={brokers}
          close={() => setPanel(null)}
        />
      )}
      {panel === "test" && panelConfig && (
        <DraftTest config={panelConfig} id={id} close={() => setPanel(null)} />
      )}
    </section>
  );
}
