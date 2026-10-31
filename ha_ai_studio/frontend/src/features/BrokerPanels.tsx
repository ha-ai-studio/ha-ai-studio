import { useEffect, useState } from "react";
import {
  Button,
  Checkbox,
  Input,
  LogView,
  Notice,
  SidePanel,
  Status,
} from "../design-system";
import { api, errorText } from "../lib/api";
import { useOperation } from "../lib/hooks";
import type { Broker, BrokerProbe, Brokers, Discovery } from "../lib/types";
export function BrokerPanel({
  broker,
  brokers,
  discovery: initialDiscovery,
  close,
  saved,
}: {
  broker: Broker | null;
  brokers: Brokers;
  discovery: Discovery;
  close: () => void;
  saved: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(broker);
  const [name, setName] = useState(broker?.name ?? "");
  const [host, setHost] = useState(broker?.host ?? "");
  const [port, setPort] = useState(broker?.port ?? 1883);
  const [tls, setTls] = useState(broker?.tls ?? false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [clear, setClear] = useState(false);
  const [discovery, setDiscovery] = useState(initialDiscovery);
  const [enabled, setEnabled] = useState(
    !!broker &&
      initialDiscovery.config.broker_id === broker.id &&
      initialDiscovery.config.enabled,
  );
  const [prefix, setPrefix] = useState(discovery.config.prefix);
  const [birth, setBirth] = useState(discovery.config.ha_status_topic);
  const op = useOperation();
  const readonly = !!editing && !editing.editable;
  async function save() {
    for (const topic of [prefix, birth])
      if (
        !topic ||
        new TextEncoder().encode(topic).length > 128 ||
        topic.startsWith("$") ||
        /[#+\x00-\x1f]/.test(topic) ||
        topic.endsWith("/")
      )
        throw new Error(
          "Use concrete discovery topics without wildcards or a trailing slash.",
        );
    let target = editing;
    if (!readonly) {
      const data = await api("brokers", {
        id: editing?.id ?? null,
        name,
        host,
        port,
        tls,
        username: clear ? "" : username || null,
        password: clear ? "" : password || null,
      });
      target =
        data.brokers.find((b) =>
          editing
            ? b.id === editing.id
            : !brokers.brokers.some((old) => old.id === b.id),
        ) ?? null;
      if (!target)
        throw new Error(
          "Broker saved, but could not reload its settings. Reopen the panel to configure discovery.",
        );
      setEditing(target);
      setPassword("");
      setUsername("");
      setClear(false);
    }
    if (target && (enabled || discovery.config.broker_id === target.id)) {
      try {
        setDiscovery(
          await api("discovery", {
            enabled,
            broker_id: enabled ? target.id : null,
            prefix,
            ha_status_topic: birth,
          }),
        );
      } catch (error) {
        throw new Error(
          "Connection settings saved. Discovery settings were not saved: " +
            errorText(error),
        );
      }
    }
    await saved();
    close();
  }
  return (
    <SidePanel
      title={broker ? "Edit broker" : "Add broker"}
      onClose={close}
      busy={op.busy}
      footer={
        <>
          {enabled && (
            <Button
              className="footer-secondary"
              disabled={op.busy}
              onClick={() =>
                void op.run(async () => setDiscovery(await api("discovery")))
              }
            >
              Refresh status
            </Button>
          )}
          <Button
            form="broker-form"
            type="submit"
            variant="primary"
            disabled={op.busy}
          >
            Save broker
          </Button>
        </>
      }
    >
      <form
        id="broker-form"
        onSubmit={(e) => {
          e.preventDefault();
          void op.run(save);
        }}
      >
        <fieldset disabled={op.busy || readonly}>
          <Input
            label="Name"
            required
            maxLength={80}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Input
            label="Host"
            required
            autoComplete="off"
            value={host}
            onChange={(e) => setHost(e.target.value)}
          />
          <Input
            label="Port"
            required
            type="number"
            min={1}
            max={65535}
            value={port}
            onChange={(e) => setPort(Number(e.target.value))}
          />
          <Input
            label="Username"
            autoComplete="off"
            placeholder="Leave empty to keep stored value"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <Input
            label="Password"
            type="password"
            autoComplete="new-password"
            placeholder="Leave empty to keep stored value"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {editing && (
            <p className="small muted">
              Username {editing.secrets.username ? "stored" : "not set"} ·
              Password {editing.secrets.password ? "stored" : "not set"}
            </p>
          )}
          <Checkbox
            label="Use TLS"
            checked={tls}
            onChange={(e) => setTls(e.target.checked)}
          />
          <Checkbox
            label="Clear saved credentials"
            checked={clear}
            onChange={(e) => setClear(e.target.checked)}
          />
        </fieldset>
        <fieldset disabled={op.busy}>
          <Checkbox
            label="Enable MQTT Discovery"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          <p className="small muted">
            Only explicitly published virtual Devices and their sensors are
            announced. Actions remain internal. Old owned Action discovery
            entries are cleaned up automatically.
          </p>
          {discovery.config.enabled &&
            discovery.config.broker_id !== editing?.id && (
              <p className="small muted">
                Currently enabled on{" "}
                {brokers.brokers.find(
                  (b) => b.id === discovery.config.broker_id,
                )?.name ?? "another broker"}
                . Enabling here moves discovery to this broker.
              </p>
            )}
          <Input
            label="Discovery prefix"
            required
            maxLength={128}
            value={prefix}
            onChange={(e) => setPrefix(e.target.value)}
          />
          <Input
            label="Home Assistant status topic"
            required
            maxLength={128}
            value={birth}
            onChange={(e) => setBirth(e.target.value)}
          />
          {enabled && <p role="status">{discovery.status}</p>}
          {(!!discovery.pending_cleanup || discovery.legacy_pending) && (
            <Notice>
              {discovery.legacy_pending
                ? "Previous discovery records found. Save discovery settings to reconcile them."
                : `${discovery.pending_cleanup} owned messages awaiting cleanup. Keep the previous broker available; cleanup retries automatically before new discovery is published.`}
            </Notice>
          )}
        </fieldset>
        <Notice error>{op.error}</Notice>
      </form>
    </SidePanel>
  );
}
export function BrokerTestPanel({
  broker,
  close,
}: {
  broker: Broker;
  close: () => void;
}) {
  const [result, setResult] = useState<BrokerProbe>();
  const [error, setError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    void api("broker-test", { id: broker.id }, undefined, abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setResult(value);
      })
      .catch((error) => {
        if (!abort.signal.aborted) setError(errorText(error));
      });
    return () => abort.abort();
  }, [broker.id]);
  return (
    <SidePanel
      title="Test MQTT connection"
      onClose={close}
      logs
      footer={
        <p className="small muted">
          {result?.scope ??
            "No MQTT messages will be published. Credentials are hidden."}
        </p>
      }
    >
      <Status
        name={broker.name}
        status={error || result?.message || "Testing connection…"}
        tone={
          error || result?.ok === false
            ? "error"
            : result?.ok
              ? "success"
              : "pending"
        }
      />
      <LogView label="Connection test log">
        {error
          ? "The diagnostic request could not be completed."
          : result
            ? result.logs
                .map(
                  (e) => `[${(e.elapsed_ms / 1000).toFixed(2)}s] ${e.message}`,
                )
                .join("\n")
            : "Connecting from the app container. Waiting for diagnostic results…"}
      </LogView>
    </SidePanel>
  );
}
