import { useEffect, useState, type ReactNode } from "react";
import {
  Button,
  Card,
  Checkbox,
  DataTable,
  Input,
  LogView,
  Notice,
  Select,
  SidePanel,
  Status,
} from "../design-system";
import { api, errorText } from "../lib/api";
import { useOperation, usePoll } from "../lib/hooks";
import type {
  ActionState,
  DeviceConfig,
  DeviceSensor,
  DeviceFunction,
  DeviceState,
} from "../lib/types";
import { ConfirmDelete } from "./ActionPanels";
const date = (value?: string | null) =>
  value ? new Date(value).toLocaleString() : "No reading yet";

export function DevicesPage({ open }: { open: (id: string) => void }) {
  const [items, setItems] = useState<DeviceState[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [create, setCreate] = useState(false);
  const [name, setName] = useState("");
  const [remove, setRemove] = useState<DeviceState>();
  const op = useOperation();
  async function refresh(signal?: AbortSignal) {
    try {
      const data = await api("devices", undefined, undefined, signal);
      if (!signal?.aborted) {
        setItems(data.devices);
        setLoaded(true);
        setError("");
      }
    } catch (e) {
      if (!signal?.aborted) setError(errorText(e));
    }
  }
  useEffect(() => {
    const c = new AbortController();
    void refresh(c.signal);
    return () => c.abort();
  }, []);
  usePoll(refresh, !create && !remove && !op.busy);
  return (
    <section>
      <div className="heading">
        <div>
          <h1>Devices</h1>
          <p>
            Virtual devices with scheduled measurements and Home Assistant
            sensors and functions.
          </p>
        </div>
        <Button variant="primary" onClick={() => setCreate(true)}>
          Create device
        </Button>
      </div>
      <Notice error>{error || op.error}</Notice>
      <DataTable
        label="Devices"
        columns={["Name", "Measurements", "Last update", "Status", "Actions"]}
      >
        {items.map((item) => (
          <tr key={item.id}>
            <td>{item.draft.name}</td>
            <td>
              {(item.active?.sensors || []).map((sensor) => (
                <div key={sensor.id}>
                  {sensor.name}: {item.values[sensor.id]?.value ?? "—"}{" "}
                  {sensor.unit}
                </div>
              ))}
            </td>
            <td>
              {date(
                Object.values(item.values)
                  .map((v) => v.timestamp)
                  .filter(Boolean)
                  .sort()
                  .at(-1),
              )}
            </td>
            <td>{item.status}</td>
            <td className="row-actions">
              <Button onClick={() => open(item.id)}>Edit</Button>
              <Button
                disabled={item.busy || op.busy}
                variant="danger"
                onClick={() => setRemove(item)}
              >
                Delete
              </Button>
            </td>
          </tr>
        ))}
      </DataTable>
      {!loaded && !error && <p role="status">Loading devices…</p>}
      {loaded && !items.length && (
        <p>
          Create a virtual device, assign its measurement actions, and activate
          it when ready.
        </p>
      )}
      {create && (
        <SidePanel
          title="Create device"
          busy={op.busy}
          onClose={() => setCreate(false)}
          footer={
            <Button
              form="create-device"
              type="submit"
              variant="primary"
              disabled={op.busy}
            >
              Create device
            </Button>
          }
        >
          <form
            id="create-device"
            onSubmit={(e) => {
              e.preventDefault();
              void op.run(async () => {
                const result = (await api("devices", {
                  name,
                })) as unknown as DeviceState;
                setCreate(false);
                open(result.id);
              });
            }}
          >
            <fieldset disabled={op.busy}>
              <Input
                label="Name"
                required
                maxLength={80}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </fieldset>
            <Notice error>{op.error}</Notice>
          </form>
        </SidePanel>
      )}
      {remove && (
        <ConfirmDelete
          title="Delete device"
          description={`Delete “${remove.draft.name}”?`}
          note="Measurements will stop. Studio will retry cleanup of its owned Home Assistant discovery topics. Saved device records are kept."
          close={() => setRemove(undefined)}
          remove={async () => {
            await api("delete-device", { id: remove.id, confirmed: true });
            setRemove(undefined);
            await refresh();
          }}
        />
      )}
    </section>
  );
}

export function DeviceEditor({
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
  const [item, setItem] = useState<DeviceState>();
  const [config, setConfig] = useState<DeviceConfig>();
  const [actions, setActions] = useState<ActionState[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [enabled, setEnabled] = useState(false);
  const op = useOperation();
  const dirty =
    !!item &&
    (JSON.stringify(config) !== JSON.stringify(item.draft) ||
      enabled !== item.enabled);
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);
  useEffect(() => {
    onBusy(op.busy);
    return () => onBusy(false);
  }, [op.busy, onBusy]);
  useEffect(() => {
    const c = new AbortController();
    void Promise.all([
      api("device", undefined, id, c.signal),
      api("actions", undefined, undefined, c.signal),
    ])
      .then(([device, data]) => {
        if (!c.signal.aborted) {
          setItem(device);
          setConfig(device.draft);
          setEnabled(device.enabled);
          setActions(data.actions.filter((a) => a.supported));
        }
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(errorText(e));
      });
    return () => c.abort();
  }, [id]);
  usePoll(
    async (signal) => {
      try {
        const device = await api("device", undefined, id, signal);
        if (!signal.aborted) {
          setEnabled((current) =>
            current === item?.enabled ? device.enabled : current,
          );
          setItem(device);
          setError("");
        }
      } catch (e) {
        if (!signal.aborted) setError(errorText(e));
      }
    },
    !op.busy,
    item?.busy ? 500 : 2000,
  );
  if (!item || !config)
    return (
      <>
        <Notice error>{error}</Notice>
        <p>Loading device…</p>
      </>
    );
  const locked = op.busy || item.busy;
  const pendingActivation =
    !!item.active && JSON.stringify(config) !== JSON.stringify(item.active);
  const change = (patch: Partial<DeviceConfig>) => {
    setConfig({ ...config, ...patch });
    setMessage("");
  };
  const functions = config.functions || [];
  const functionActions = actions.filter((a) =>
    a.plugin?.functions?.includes("execute"),
  );
  const functionChange = (index: number, patch: Partial<DeviceFunction>) =>
    change({
      functions: functions.map((f, i) =>
        i === index ? { ...f, ...patch } : f,
      ),
    });
  const sensorChange = (index: number, patch: Partial<DeviceSensor>) =>
    change({
      sensors: config.sensors.map((s, i) =>
        i === index ? { ...s, ...patch } : s,
      ),
    });
  const save = () =>
    op.run(async () => {
      const result = await api("device", { config, enabled }, id);
      setItem(result);
      setConfig(result.draft);
      setEnabled(result.enabled);
      setMessage(
        result.enabled
          ? result.active?.sensors.length
            ? "Settings saved. Device is active; the next measurement is scheduled."
            : "Settings saved. Device is active; functions are available on demand."
          : "Settings saved. Device is inactive.",
      );
    });
  return (
    <section className="device-editor">
      <Button variant="text" onClick={back} disabled={op.busy}>
        ← Devices
      </Button>
      <div className="heading">
        <div>
          <h1>{config.name}</h1>
          <p>
            Manage sensors, functions, automatic readings and Home Assistant
            publication.
          </p>
        </div>
        <div className="actions">
          <Button
            disabled={
              locked ||
              !item.enabled ||
              dirty ||
              pendingActivation ||
              !item.active?.sensors.length
            }
            onClick={() =>
              void op.run(async () => {
                setItem(await api("device-read", {}, id));
              })
            }
          >
            Read now
          </Button>
        </div>
      </div>
      <Notice error>{error || op.error || item.issue}</Notice>
      <Notice>{message}</Notice>
      <Notice>
        {dirty || (item.enabled && pendingActivation)
          ? "Unsaved changes. Save to apply settings and the active state. Scheduled readings use the previously saved configuration."
          : null}
      </Notice>
      <Card title="Device status">
        <Status
          name={item.active?.name || config.name}
          status={item.status}
          tone={
            item.status === "ready"
              ? "success"
              : ["error", "partial", "interrupted"].includes(item.status)
                ? "error"
                : "pending"
          }
          detail={item.publication}
        >
          <div className="device-timing small muted">
            <span>Last attempt: {date(item.last_attempt)}</span>
            <span>
              Next update:{" "}
              {item.next_run
                ? new Date(item.next_run * 1000).toLocaleString()
                : "Not scheduled"}
            </span>
          </div>
        </Status>
        <div className="device-measurement-log">
          <h3>Measurement log</h3>
          <LogView label="Measurement log" live>
            {item.logs?.length
              ? item.logs
                  .map(
                    (entry) =>
                      `${new Date(entry.time).toLocaleTimeString()}  ${entry.message}`,
                  )
                  .join("\n")
              : "No measurement log yet. Select Read now to follow the next measurement. Logs are cleared when Studio restarts."}
          </LogView>
        </div>
      </Card>
      <form
        id="device-form"
        onInvalidCapture={(event) => {
          let parent = (event.target as HTMLElement).parentElement;
          while (parent && parent !== event.currentTarget) {
            if (parent instanceof HTMLDetailsElement) parent.open = true;
            parent = parent.parentElement;
          }
        }}
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <fieldset disabled={locked}>
          <Card title="Device settings">
            <Checkbox
              label="Active"
              checked={enabled}
              onChange={(event) => {
                setEnabled(event.target.checked);
                setMessage("");
              }}
            />
            <p className="small muted">
              Enable scheduled readings and configured functions. Changes take
              effect when you save.
            </p>
            <div className="device-field-grid">
              <Input
                label="Device name"
                value={config.name}
                required
                maxLength={80}
                onChange={(e) => change({ name: e.target.value })}
              />
              <Input
                label="Update interval (minutes)"
                type="number"
                min={1}
                max={10080}
                step={1}
                required
                value={config.interval_minutes}
                onChange={(e) =>
                  change({ interval_minutes: Number(e.target.value) })
                }
              />
            </div>
            <Checkbox
              label="Publish this device to Home Assistant"
              checked={config.publish}
              onChange={(e) => change({ publish: e.target.checked })}
            />
            <p className="muted">
              Publishes all sensors and function buttons using MQTT Discovery
              configured in Settings.
            </p>
          </Card>
          <div className="heading sensor-section-heading">
            <div>
              <h2>
                Sensors <span className="muted">({config.sensors.length})</span>
              </h2>
              <p>
                Each sensor displays a number or text supplied by an Action.
              </p>
            </div>
            <Button
              className="sensor-add-button"
              aria-label="Add sensor"
              title="Add sensor"
              disabled={config.sensors.length >= 16}
              onClick={() =>
                change({
                  sensors: [
                    ...config.sensors,
                    {
                      id: Array.from(
                        { length: 17 },
                        (_, i) => `measurement-${i + 1}`,
                      ).find((id) => !config.sensors.some((s) => s.id === id))!,
                      name: "Sensor",
                      device_class: "",
                      cumulative: false,
                      unit: "",
                      action_id: "",
                      reading: "",
                      max_change: null,
                    },
                  ],
                })
              }
            >
              <span aria-hidden="true">+</span>
            </Button>
          </div>
          {!config.sensors.length && (
            <p className="sensor-empty">
              Add a sensor to choose what this device reads.
            </p>
          )}
          {config.sensors.map((sensor, index) => {
            const selected = actions.find(
              (a) => a.definition.id === sensor.action_id,
            );
            const settings = (
              selected?.enabled
                ? selected.config?.active?.settings
                : selected?.config?.draft.settings
            ) as { meters?: { id: string; name?: string }[] } | undefined;
            const meters = Array.isArray(settings?.meters)
              ? settings.meters.filter(
                  (m) =>
                    m &&
                    typeof m.id === "string" &&
                    (m.name === undefined || typeof m.name === "string"),
                )
              : [];
            const value = item.values[sensor.id];
            const activeUnit =
              item.active?.sensors.find((s) => s.id === sensor.id)?.unit ||
              sensor.unit;
            return (
              <SensorCard
                key={index}
                name={sensor.name || `Sensor ${index + 1}`}
                reading={
                  value?.value === undefined
                    ? "No reading yet"
                    : `${value.value} ${activeUnit}`.trim()
                }
                source={selected?.definition.name || "Choose an Action"}
                error={value?.error}
                initiallyOpen={!sensor.action_id || !sensor.reading}
              >
                <div className="device-field-grid">
                  <Input
                    label="Sensor name"
                    required
                    value={sensor.name}
                    maxLength={80}
                    onChange={(e) =>
                      sensorChange(index, { name: e.target.value })
                    }
                  />
                  <Input
                    label="Unit"
                    hint="Leave empty for text values such as serial numbers."
                    value={sensor.unit}
                    onChange={(e) =>
                      sensorChange(index, { unit: e.target.value })
                    }
                  />
                </div>
                <div className="device-field-grid">
                  <Select
                    label="Action"
                    required
                    value={sensor.action_id}
                    onChange={(e) =>
                      sensorChange(index, {
                        action_id: e.target.value,
                        reading: "",
                      })
                    }
                  >
                    <option value="">Select an action</option>
                    {actions.map((a) => (
                      <option key={a.definition.id} value={a.definition.id}>
                        {a.definition.name}
                      </option>
                    ))}
                  </Select>
                  {meters.length ? (
                    <Select
                      label="Action reading"
                      required
                      value={sensor.reading}
                      onChange={(e) =>
                        sensorChange(index, { reading: e.target.value })
                      }
                    >
                      <option value="">Select a reading</option>
                      {meters.map((m) => (
                        <option value={m.id} key={m.id}>
                          {m.name || m.id}
                        </option>
                      ))}
                    </Select>
                  ) : (
                    <Input
                      label="Action reading ID"
                      required
                      pattern={"[a-z][a-z0-9\\-]{0,47}"}
                      value={sensor.reading}
                      hint="The named reading supplied by this action."
                      onChange={(e) =>
                        sensorChange(index, { reading: e.target.value })
                      }
                    />
                  )}
                </div>
                <details className="sensor-advanced">
                  <summary>Advanced settings</summary>
                  <p className="small muted">
                    Stable identity, Home Assistant classification and numeric
                    validation.
                  </p>
                  <div className="device-field-grid">
                    <Input
                      label="Sensor ID"
                      required
                      pattern={"[a-z][a-z0-9\\-]{0,47}"}
                      value={sensor.id}
                      hint="Stable identity for Home Assistant history. Use a new ID when replacing a meter."
                      onChange={(e) =>
                        sensorChange(index, { id: e.target.value })
                      }
                    />
                    <Input
                      label="Home Assistant device class"
                      value={sensor.device_class}
                      maxLength={64}
                      pattern="[a-z][a-z0-9_]{0,63}"
                      hint="Optional HA classification, such as water. Studio does not interpret it. Leave empty for a generic sensor."
                      onChange={(e) =>
                        sensorChange(index, { device_class: e.target.value })
                      }
                    />
                  </div>
                  <Checkbox
                    label="Cumulative counter (value must not decrease)"
                    checked={sensor.cumulative}
                    onChange={(e) =>
                      sensorChange(index, { cumulative: e.target.checked })
                    }
                  />
                  <Input
                    label="Maximum change per reading"
                    type="number"
                    min="0"
                    step="any"
                    value={sensor.max_change ?? ""}
                    hint="Optional, in the sensor’s unit. Reject larger changes while keeping the last valid value."
                    onChange={(e) =>
                      sensorChange(index, {
                        max_change:
                          e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                  />
                </details>
                <div className="sensor-card-footer">
                  <span className="small muted">
                    Last valid reading: {date(value?.timestamp)}
                  </span>
                  <Button
                    variant="danger"
                    onClick={() =>
                      change({
                        sensors: config.sensors.filter((_, i) => i !== index),
                      })
                    }
                  >
                    Remove sensor
                  </Button>
                </div>
              </SensorCard>
            );
          })}
          <div className="heading sensor-section-heading">
            <div>
              <h2>
                Functions <span className="muted">({functions.length})</span>
              </h2>
              <p>
                Add calls to individual Actions. Execute updates this device’s
                sensors backed by the selected Action.
              </p>
            </div>
            <Button
              disabled={functions.length >= 16}
              onClick={() =>
                change({
                  functions: [
                    ...functions,
                    {
                      id: Array.from(
                        { length: 17 },
                        (_, i) => `function-${i + 1}`,
                      ).find((id) => !functions.some((f) => f.id === id))!,
                      name: "Execute",
                      action_id: "",
                      function: "execute",
                    },
                  ],
                })
              }
            >
              Add function
            </Button>
          </div>
          {!functions.length && (
            <p className="muted">
              No functions configured. Add only the calls you want this device
              to offer.
            </p>
          )}
          {functions.map((fn, index) => (
            <SensorCard
              key={index}
              name={fn.name || `Function ${index + 1}`}
              reading="Execute"
              source={
                actions.find((a) => a.definition.id === fn.action_id)
                  ?.definition.name || "Choose an Action"
              }
              initiallyOpen={!fn.action_id}
            >
              <div className="device-field-grid">
                <Input
                  label="Function name"
                  required
                  maxLength={80}
                  value={fn.name}
                  onChange={(e) =>
                    functionChange(index, { name: e.target.value })
                  }
                />
                <Select
                  label="Function action"
                  required
                  value={fn.action_id}
                  onChange={(e) =>
                    functionChange(index, { action_id: e.target.value })
                  }
                >
                  <option value="">Select an action</option>
                  {functionActions.map((a) => (
                    <option key={a.definition.id} value={a.definition.id}>
                      {a.definition.name}
                    </option>
                  ))}
                </Select>
              </div>
              <details className="sensor-advanced">
                <summary>Advanced settings</summary>
                <Input
                  label="Function ID"
                  required
                  pattern={"[a-z][a-z0-9\\-]{0,47}"}
                  value={fn.id}
                  hint="Stable identity for the Home Assistant button."
                  onChange={(e) =>
                    functionChange(index, { id: e.target.value })
                  }
                />
              </details>
              {!functionActions.length && (
                <Notice>
                  No Actions support Execute yet. Add a compatible Action.
                </Notice>
              )}
              <div className="sensor-card-footer">
                <Button
                  disabled={
                    locked || !item.enabled || dirty || pendingActivation
                  }
                  onClick={() =>
                    void op.run(async () => {
                      setItem(
                        await api(
                          "device-function",
                          {
                            function_id: fn.id,
                            request_id: Array.from(
                              crypto.getRandomValues(new Uint32Array(4)),
                              (n) => n.toString(16).padStart(8, "0"),
                            ).join(""),
                          },
                          id,
                        ),
                      );
                    })
                  }
                >
                  Run function
                </Button>
                <Button
                  variant="danger"
                  onClick={() =>
                    change({
                      functions: functions.filter((_, i) => i !== index),
                    })
                  }
                >
                  Remove function
                </Button>
              </div>
            </SensorCard>
          ))}
        </fieldset>
        {enabled && !config.sensors.length && !functions.length && (
          <Notice>
            Add at least one sensor or function to enable this device.
          </Notice>
        )}
        <div className="actions device-footer">
          <Button
            type="submit"
            variant="primary"
            disabled={
              locked || (enabled && !config.sensors.length && !functions.length)
            }
          >
            Save
          </Button>
        </div>
      </form>
    </section>
  );
}

function SensorCard({
  name,
  reading,
  source,
  error,
  initiallyOpen,
  children,
}: {
  name: string;
  reading: string;
  source: string;
  error?: string;
  initiallyOpen: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <article className="sensor-card">
      <details
        open={open}
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary>
          <span className="sensor-card-name">
            {name}
            <small>{source}</small>
          </span>
          <span className="sensor-card-value">{reading}</span>
        </summary>
        <div className="sensor-card-body">{children}</div>
      </details>
      <Notice error>{error}</Notice>
    </article>
  );
}
