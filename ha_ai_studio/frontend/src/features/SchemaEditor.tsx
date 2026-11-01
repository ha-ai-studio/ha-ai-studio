import {
  EntityPicker,
  EntitySearchField,
  type EntityPickerOptions,
  type HAEntity,
} from "./EntityPicker";
import { useEffect, useRef, useState } from "react";
import { Checkbox, Input, Select, Textarea } from "../design-system";
import { appUrl } from "../lib/api";
import type { Json, Schema } from "../lib/types";
export function simpleSchema(schema: Schema): boolean {
  return (
    schema.type === "object" &&
    schema.additionalProperties === false &&
    !schema.patternProperties &&
    Object.values(schema.properties ?? {}).every((p) =>
      ["string", "number", "integer", "boolean"].includes(p.type ?? ""),
    )
  );
}
// Defaults populate only missing numeric draft fields; persisted settings are untouched.
function settingsWithDefaults(schema: Schema, initial: Json): Record<string, Json> {
  const settings: Record<string, Json> =
    initial && typeof initial === "object" && !Array.isArray(initial)
      ? { ...initial } : {};
  for (const [key, property] of Object.entries(schema.properties ?? {})) {
    if (!(key in settings) && !property.enum &&
        ["number", "integer"].includes(property.type ?? "") &&
        typeof property.default === "number" && Number.isFinite(property.default) &&
        (property.type !== "integer" || Number.isInteger(property.default))) {
      settings[key] = property.default;
    }
  }
  return settings;
}

export function readSettings(
  schema: Schema,
  initial: Json,
  values: Record<string, string | boolean>,
  raw: string,
): Json {
  if (!simpleSchema(schema)) {
    try {
      return JSON.parse(raw) as Json;
    } catch {
      throw new Error("Provide valid settings JSON.");
    }
  }
  const result = settingsWithDefaults(schema, initial);
  for (const [key, property] of Object.entries(schema.properties ?? {})) {
    if (!(key in values)) {
      if (
        schema.required?.includes(key) &&
        ["number", "integer"].includes(property.type ?? "") &&
        !(key in result)
      )
        throw new Error(`${property.title ?? key} is required.`);
      continue;
    }
    const value = values[key];
    if (property.enum) {
      if (value === "") delete result[key];
      else result[key] = property.enum[Number(value)];
    } else if (property.type === "number" || property.type === "integer") {
      if (value === "") {
        if (schema.required?.includes(key))
          throw new Error(`${property.title ?? key} is required.`);
        delete result[key];
      } else {
        const number = Number(value);
        if (
          !Number.isFinite(number) ||
          (property.type === "integer" && !Number.isInteger(number))
        )
          throw new Error(
            `${property.title ?? key} must be a valid ${property.type}.`,
          );
        result[key] = number;
      }
    } else result[key] = value;
  }
  return result;
}
export function SchemaEditor({
  schema,
  initial,
  values,
  raw,
  onValues,
  onRaw,
}: {
  schema: Schema;
  initial: Json;
  values: Record<string, string | boolean>;
  raw: string;
  onValues: (values: Record<string, string | boolean>) => void;
  onRaw: (raw: string) => void;
}) {
  const [entityField, setEntityField] = useState<{ key: string; options: EntityPickerOptions } | null>(null);
  const settings = settingsWithDefaults(schema, initial);
  return (
    <>
      <p>
        Settings are validated against the action schema. Git action preview is
        unavailable; use an isolated MQTT test instance.
      </p>
      {entityField && <EntityPicker options={entityField.options}
        onClose={() => setEntityField(null)}
        onSelect={(entity) => {
          onValues({...values, [entityField.key]: entity.entity_id});
          setEntityField(null);
        }} />}
      {!simpleSchema(schema) ? (
        <Textarea
          label="Settings JSON"
          rows={12}
          value={raw}
          onChange={(e) => onRaw(e.target.value)}
        />
      ) : (
        Object.entries(schema.properties ?? {}).map(([key, p]) => {
          const label = p.title ?? key;
          const change = (value: string | boolean) =>
            onValues({ ...values, [key]: value });
          if (p.type === "string" && p["x-ha-entity-domains"]?.length)
            return (
              <EntitySearchField key={key} label={label} hint={p.description}
                value={String(values[key] ?? settings[key] ?? "")}
                onClear={() => change("")}
                onChoose={() => setEntityField({key, options: {
                  domains: p["x-ha-entity-domains"]!, title: `Select ${label}`,
                }})} />
            );
          if (p.enum)
            return (
              <Select
                key={key}
                label={label}
                value={String(
                  values[key] ??
                    (key in settings ? p.enum.indexOf(settings[key]) : ""),
                )}
                onChange={(e) => change(e.target.value)}
              >
                <option value="">Select a value</option>
                {p.enum.map((v, i) => (
                  <option key={i} value={i}>
                    {String(v)}
                  </option>
                ))}
              </Select>
            );
          if (p.type === "boolean")
            return (
              <Checkbox
                key={key}
                label={label}
                checked={Boolean(values[key] ?? settings[key] ?? false)}
                onChange={(e) => change(e.target.checked)}
              />
            );
          return (
            <Input
              key={key}
              label={label}
              hint={p.description}
              type={
                ["number", "integer"].includes(p.type ?? "") ? "number" : "text"
              }
              step={p.type === "integer" ? 1 : "any"}
              value={String(values[key] ?? settings[key] ?? "")}
              onChange={(e) =>
                change(e.target.validity.badInput ? "NaN" : e.target.value)
              }
            />
          );
        })
      )}
    </>
  );
}
interface EditorContext {
  pickEntity: (options: EntityPickerOptions) => Promise<HAEntity | null>;
  changed: () => void;
  uploadImage: (
    file: File,
  ) => Promise<{ id: string; width: number; height: number }>;
  assetUrl: (id: string) => string;
  removeImage: (id: string) => Promise<unknown>;
  preview: (data: Json, settings: Json) => Promise<Json>;
  cameraTest: (
    data: Json,
    settings: Json,
    progress?: (entries: { time: string; message: string }[]) => void,
    captureOnly?: boolean,
  ) => Promise<Json>;
  previewImageUrl: (run: string, name: string) => string;
}
interface EditorModule {
  render: (
    host: HTMLElement,
    settings: Json,
    context: EditorContext,
  ) => (() => Json) | { read: () => Json; dispose: () => void };
}
// Trusted editor modules own this DOM island; React owns the surrounding form.
export function LegacyEditor({
  type,
  editor,
  actionId,
  onBusy,
  settings,
  onReady,
  onError,
  onChange,
}: {
  type: string;
  editor?: string | null;
  actionId?: string;
  onBusy?: (busy: boolean) => void;
  settings: Json;
  onReady: (read: (() => Json) | null) => void;
  onError: (message: string) => void;
  onChange: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [picker, setPicker] = useState<EntityPickerOptions | null>(null);
  const pickerResolve = useRef<((entity: HAEntity | null) => void) | null>(
    null,
  );
  function finishPicker(entity: HAEntity | null) {
    pickerResolve.current?.(entity);
    pickerResolve.current = null;
    setPicker(null);
  }
  const callbacks = useRef({ onReady, onError, onChange, onBusy });
  callbacks.current = { onReady, onError, onChange, onBusy };
  useEffect(() => {
    let cancelled = false;
    let dispose: (() => void) | undefined;
    const abort = new AbortController();
    let pending = 0;
    function endpoint(name: string, query: Record<string, string> = {}) {
      if (!actionId) throw new Error("This editor has no instance context.");
      const value = appUrl(`api/${name}`);
      value.searchParams.set("id", actionId);
      for (const [key, item] of Object.entries(query))
        value.searchParams.set(key, item);
      return value.href;
    }
    async function post(name: string, value: unknown) {
      pending++;
      callbacks.current.onBusy?.(true);
      try {
        const response = await fetch(endpoint(name), {
          method: "POST",
          signal: abort.signal,
          headers: {
            "Content-Type": "application/json",
            "X-HA-AI-Studio-Request": "1",
          },
          body: JSON.stringify(value),
        });
        const body = await response.json();
        if (!response.ok)
          throw new Error(body.error || "Editor request failed.");
        return body;
      } finally {
        pending--;
        if (!cancelled) callbacks.current.onBusy?.(pending > 0);
      }
    }
    const element = host.current!;
    callbacks.current.onReady(null);
    const url = appUrl(
      editor || `action-types/${encodeURIComponent(type)}/editor.js`,
    ).href;
    import(/* @vite-ignore */ url)
      .then((module: EditorModule) => {
        if (!cancelled) {
          const result = module.render(element, structuredClone(settings), {
            pickEntity: (options) =>
              new Promise((resolve) => {
                pickerResolve.current?.(null);
                pickerResolve.current = resolve;
                setPicker(options);
              }),
            removeImage: (id) => post("action-asset-delete", { id }),
            assetUrl: (id) => endpoint("action-asset", { asset: id }),
            previewImageUrl: (run, name) =>
              endpoint("preview-image", { run, name }),
            uploadImage: async (file) => {
              if (file.size > 8 * 1024 * 1024)
                throw new Error("Image must be at most 8 MiB.");
              const bytes = new Uint8Array(await file.arrayBuffer());
              let binary = "";
              for (let i = 0; i < bytes.length; i += 8192)
                binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
              return await post("action-asset", { base64: btoa(binary) });
            },
            cameraTest: async (data, draft, progress, captureOnly = false) => {
              const run = crypto.randomUUID().replaceAll("-", "");
              let stopped = false;
              let seen = 0;
              let timer: ReturnType<typeof setTimeout> | undefined;
              const readProgress = async () => {
                try {
                  const response = await fetch(
                    endpoint("camera-test-progress", { run }),
                    { signal: abort.signal },
                  );
                  if (response.ok && !cancelled) {
                    const body = await response.json();
                    if (!cancelled && body.entries.length >= seen) {
                      seen = body.entries.length;
                      progress?.(body.entries);
                    }
                  }
                } catch {
                  /* The execution response owns errors; polling is best effort. */
                }
              };
              const poll = async () => {
                await readProgress();
                if (!stopped && !cancelled) timer = setTimeout(poll, 500);
              };
              void poll();
              try {
                return await post(captureOnly ? "action-camera-reference" : "action-camera-test", {
                  data,
                  settings: draft,
                  progress_id: run,
                });
              } finally {
                stopped = true;
                clearTimeout(timer);
                await readProgress();
              }
            },
            preview: (data, draft) =>
              post("action-preview", { data, settings: draft }),
            changed: () => {
              if (!cancelled) callbacks.current.onChange();
            },
          });
          const read = typeof result === "function" ? result : result?.read;
          dispose = typeof result === "object" ? result.dispose : undefined;
          if (typeof read !== "function")
            throw new Error("Action editor must return a settings reader.");
          callbacks.current.onReady(read);
        }
      })
      .catch(() => {
        if (!cancelled)
          callbacks.current.onError(
            "Action editor failed to load. Check its module and render contract.",
          );
      });
    return () => {
      cancelled = true;
      abort.abort();
      pickerResolve.current?.(null);
      pickerResolve.current = null;
      setPicker(null);
      callbacks.current.onBusy?.(false);
      try {
        dispose?.();
      } catch {
        callbacks.current.onError(
          "Action editor cleanup failed. Check its dispose contract.",
        );
      } finally {
        element.replaceChildren();
        callbacks.current.onReady(null);
      }
    };
  }, [type, editor, settings, actionId]);
  return (
    <>
      <div ref={host} />
      {picker && (
        <EntityPicker
          options={picker}
          onSelect={finishPicker}
          onClose={() => finishPicker(null)}
        />
      )}
    </>
  );
}
