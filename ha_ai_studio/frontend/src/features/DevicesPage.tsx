import { useEffect, useState } from "react";
import {
  Button,
  Card,
  Checkbox,
  DataTable,
  Input,
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
            sensors.
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
  const [confirm, setConfirm] = useState(false);
  const op = useOperation();
  const dirty = !!item && JSON.stringify(config) !== JSON.stringify(item.draft);
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
          setActions(data.actions.filter((a) => a.supported));
        }
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(errorText(e));
      });
    return () => c.abort();
  }, [id]);
  usePoll(async (signal) => {
    try {
      const device = await api("device", undefined, id, signal);
      if (!signal.aborted) {
        setItem(device);
        setError("");
      }
    } catch (e) {
      if (!signal.aborted) setError(errorText(e));
    }
  }, !op.busy && !confirm);
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
  const sensorChange = (index: number, patch: Partial<DeviceSensor>) =>
    change({
      sensors: config.sensors.map((s, i) =>
        i === index ? { ...s, ...patch } : s,
      ),
    });
  const save = (activate: boolean) =>
    op.run(async () => {
      const result = await api("device", { config, activate }, id);
      setItem(result);
      setConfig(result.draft);
      setMessage(
        activate
          ? "Device activated. The next measurement is scheduled."
          : "Draft saved. Active measurements are unchanged.",
      );
      setConfirm(false);
    });
  return (
    <section>
      <div className="heading">
        <div>
          <Button onClick={back} disabled={op.busy}>
            ← Devices
          </Button>
          <h1>{config.name}</h1>
          <p>
            Each sensor uses a named number or text reading from its assigned
            action.
          </p>
        </div>
        <div className="actions">
          <Button
            disabled={locked || !item.enabled || dirty || pendingActivation}
            onClick={() =>
              void op.run(async () => {
                setItem(await api("device-read", {}, id));
              })
            }
          >
            Read now
          </Button>
          <Button
            disabled={locked || !item.enabled}
            onClick={() =>
              void op.run(async () => {
                setItem(await api("device-deactivate", {}, id));
              })
            }
          >
            Deactivate
          </Button>
        </div>
      </div>
      <Notice error>{error || op.error || item.issue}</Notice>
      <Notice>{message}</Notice>
      <Notice>
        {pendingActivation
          ? "This draft differs from the active device. Scheduled readings still use the active configuration. Activate to apply changes."
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
        />
        <p>Last attempt: {date(item.last_attempt)}</p>
        <p>
          Next update:{" "}
          {item.next_run
            ? new Date(item.next_run * 1000).toLocaleString()
            : "Not scheduled"}
        </p>
      </Card>
      <form
        id="device-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save(false);
        }}
      >
        <fieldset disabled={locked}>
          <Card title="Configuration">
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
            <Checkbox
              label="Publish this device to Home Assistant"
              checked={config.publish}
              onChange={(e) => change({ publish: e.target.checked })}
            />
            <p className="muted">
              Uses central MQTT Discovery from Settings. Actions are never
              advertised as devices.
            </p>
          </Card>
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
              <Card
                key={index}
                title={`Measurement ${index + 1}`}
                actions={
                  <Button
                    variant="danger"
                    onClick={() =>
                      change({
                        sensors: config.sensors.filter((_, i) => i !== index),
                      })
                    }
                  >
                    Remove measurement
                  </Button>
                }
              >
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
                  label="Sensor ID"
                  required
                  pattern="[a-z][a-z0-9-]{0,47}"
                  value={sensor.id}
                  hint="Stable identity for Home Assistant history. Use a new ID when replacing a meter."
                  onChange={(e) => sensorChange(index, { id: e.target.value })}
                />
                <Input
                  label="Unit"
                  hint="Leave empty for text values such as serial numbers."
                  value={sensor.unit}
                  onChange={(e) =>
                    sensorChange(index, { unit: e.target.value })
                  }
                />
                <Checkbox
                  label="Cumulative counter (value must not decrease)"
                  checked={sensor.cumulative}
                  onChange={(e) =>
                    sensorChange(index, { cumulative: e.target.checked })
                  }
                />
                <details>
                  <summary>Optional Home Assistant metadata</summary>
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
                </details>
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
                    pattern="[a-z][a-z0-9-]{0,47}"
                    value={sensor.reading}
                    hint="The named reading supplied by this action."
                    onChange={(e) =>
                      sensorChange(index, { reading: e.target.value })
                    }
                  />
                )}
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
                <p>
                  Last valid reading: {value?.value ?? "—"} {activeUnit} ·{" "}
                  {date(value?.timestamp)}
                </p>
                <Notice error>{value?.error}</Notice>
              </Card>
            );
          })}
          <Button
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
                    name: "Measurement",
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
            Add measurement
          </Button>
        </fieldset>
        <div className="actions device-footer">
          <Button type="submit" disabled={locked}>
            Save draft
          </Button>
          <Button
            variant="primary"
            disabled={locked || !config.sensors.length}
            onClick={() => {
              const form = document.getElementById(
                "device-form",
              ) as HTMLFormElement;
              if (form.reportValidity()) setConfirm(true);
            }}
          >
            Activate device
          </Button>
        </div>
      </form>
      {confirm && (
        <SidePanel
          title="Activate device"
          onClose={() => setConfirm(false)}
          busy={op.busy}
          footer={
            <>
              <Button disabled={op.busy} onClick={() => setConfirm(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={op.busy}
                onClick={() => void save(true)}
              >
                Activate device
              </Button>
            </>
          }
        >
          <p>
            Studio will run the assigned actions every {config.interval_minutes}{" "}
            minutes using their current configurations. This may capture camera
            images and operate configured lights.
          </p>
          <p>
            {config.publish
              ? "The device and its sensors will be published through central MQTT Discovery."
              : "Home Assistant publication is disabled for this device."}
          </p>
          <Notice error>{op.error}</Notice>
        </SidePanel>
      )}
    </section>
  );
}
