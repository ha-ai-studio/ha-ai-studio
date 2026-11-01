import { useEffect, useState } from "react";
import { Button, Input, Notice, Select, SidePanel } from "../design-system";
import { api } from "../lib/api";
import { useOperation } from "../lib/hooks";
import type { ActionState, DeviceTemplate } from "../lib/types";

export async function exportDeviceTemplate(id: string) {
  const template = await api("device-template", undefined, id);
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(template, null, 2) + "\n"], {
      type: "application/json",
    }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "device-template.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ImportDeviceTemplate({
  close,
  open,
}: {
  close: () => void;
  open: (id: string) => void;
}) {
  const [template, setTemplate] = useState<DeviceTemplate>();
  const [actions, setActions] = useState<ActionState[]>([]);
  const [assignments, setAssignments] = useState<Record<string, string>>({});
  const [name, setName] = useState("");
  const op = useOperation();
  useEffect(() => {
    void op.run(async () =>
      setActions((await api("actions")).actions.filter((a) => a.supported)),
    );
  }, []);
  const entries = template
    ? [
        ...template.config.sensors.filter((s) => !s.rate),
        ...(template.config.functions || []),
      ]
    : [];
  const slots = [...new Set(entries.map((entry) => entry.action_id))];
  return (
    <SidePanel
      title="Import Device template"
      busy={op.busy}
      onClose={close}
      footer={
        <Button
          form="import-device-template"
          type="submit"
          variant="primary"
          disabled={
            op.busy || !template || slots.some((slot) => !assignments[slot])
          }
        >
          Import device
        </Button>
      }
    >
      <form
        id="import-device-template"
        onSubmit={(event) => {
          event.preventDefault();
          void op.run(async () => {
            const device = await api("device-template-import", {
              template,
              name,
              actions: assignments,
            });
            open(device.id);
            close();
          });
        }}
      >
        <fieldset disabled={op.busy}>
          <Input
            label="Template file"
            type="file"
            accept=".json,application/json"
            onChange={(event) => {
              const file = event.target.files?.[0];
              setTemplate(undefined);
              setAssignments({});
              if (!file) return;
              void op.run(async () => {
                if (file.size > 80000)
                  throw new Error(
                    "Template file must be no larger than 80 KB.",
                  );
                let value: unknown;
                try {
                  value = JSON.parse(await file.text());
                } catch {
                  throw new Error("Template file must contain valid JSON.");
                }
                const result = await api("device-template-preview", value);
                setTemplate(result);
                setName(result.config.name);
              });
            }}
          />
          {template && (
            <>
              <Input
                label="Device name"
                required
                maxLength={80}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
              <p>
                {template.config.sensors.length} sensors ·{" "}
                {template.config.functions?.length || 0} functions · Every{" "}
                {template.config.interval_minutes} minutes
              </p>
              {slots.map((slot) => {
                const needsExecute = template.config.functions?.some(
                  (fn) => fn.action_id === slot,
                );
                return (
                  <Select
                    key={slot}
                    label={`Action for ${entries
                      .filter((entry) => entry.action_id === slot)
                      .map((entry) => entry.name)
                      .join(", ")}`}
                    required
                    value={assignments[slot] || ""}
                    onChange={(event) =>
                      setAssignments({
                        ...assignments,
                        [slot]: event.target.value,
                      })
                    }
                  >
                    <option value="">Select an existing Action</option>
                    {actions
                      .filter(
                        (action) =>
                          !needsExecute ||
                          action.plugin?.functions?.includes("execute"),
                      )
                      .map((action) => (
                        <option
                          key={action.definition.id}
                          value={action.definition.id}
                        >
                          {action.definition.name}
                        </option>
                      ))}
                  </Select>
                );
              })}
              <p className="muted">
                Creates a new, inactive device with Home Assistant publication
                disabled. Review Action reading IDs before activation. Actions,
                credentials and measurement history are not imported.
              </p>
            </>
          )}
        </fieldset>
        <Notice error>{op.error}</Notice>
      </form>
    </SidePanel>
  );
}
