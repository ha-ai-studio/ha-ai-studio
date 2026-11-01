import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect, vi } from "vitest";
import { App } from "../App";
import type { DeviceState } from "../lib/types";

it("creates a device, assigns independent actions, saves the Active checkbox atomically, reads and disables without browser MQTT", async () => {
  const devices: DeviceState[] = [];
  const requests: string[] = [];
  const actions = ["Reader", "Temperature"].map((name, i) => ({
    definition: { id: `action-${i}`, name, type: "fixture" },
    supported: true,
    enabled: false,
    config: {
      draft: { settings: { meters: [{ id: "main", name: "Main reading" }] } },
      active: null,
    },
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (target: URL, options: RequestInit) => {
      const endpoint = new URL(target).pathname.split("/").at(-1)!;
      requests.push(endpoint);
      const body = options.body ? JSON.parse(String(options.body)) : undefined;
      let result: unknown;
      if (endpoint === "devices" && !body) result = { devices };
      else if (endpoint === "devices") {
        const draft = {
          name: body.name,
          interval_minutes: 30,
          publish: false,
          sensors: [],
        };
        devices.push({
          id: "fixture",
          identity: "fixture-device",
          draft,
          active: null,
          enabled: false,
          busy: false,
          status: "disabled",
          publication: "Not published",
          issue: null,
          last_attempt: null,
          next_run: null,
          values: {},
        });
        result = devices[0];
      } else if (endpoint === "actions") result = { actions, types: [] };
      else if (endpoint === "device") {
        if (body) {
          devices[0].draft = body.config;
          expect(typeof body.enabled).toBe("boolean");
          expect(body).not.toHaveProperty("activate");
          devices[0].active = structuredClone(body.config);
          devices[0].enabled = body.enabled;
          devices[0].status = body.enabled ? "ready" : "disabled";
        }
        result = devices[0];
      } else if (endpoint === "device-read") {
        devices[0].logs = [
          {
            time: "2026-10-31T12:00:00Z",
            message: "Measurement finished: ready.",
          },
        ];
        devices[0].values = {
          "measurement-1": {
            value: 123,
            timestamp: "2026-10-31T12:00:00Z",
            status: "ready",
          },
        };
        result = devices[0];
      } else if (endpoint === "device-deactivate") {
        devices[0].enabled = false;
        devices[0].status = "disabled";
        result = devices[0];
      } else if (endpoint === "delete-device") {
        devices.splice(0);
        result = { devices };
      } else throw new Error(`Unexpected endpoint ${endpoint}`);
      return { ok: true, json: async () => structuredClone(result) };
    }),
  );
  render(<App />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Create device" }),
  );
  const dialog = screen.getByRole("dialog");
  expect(
    within(dialog).queryByLabelText("Device type"),
  ).not.toBeInTheDocument();
  await userEvent.type(within(dialog).getByLabelText("Name"), "Water meter");
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Create device" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Add sensor" }),
  );
  await userEvent.selectOptions(
    screen.getByLabelText("Action", { exact: true }),
    "action-0",
  );
  await userEvent.selectOptions(
    screen.getByLabelText("Action reading", { exact: true }),
    "main",
  );
  await userEvent.click(screen.getByRole("button", { name: "Add sensor" }));
  await userEvent.selectOptions(
    screen.getAllByLabelText("Action", { exact: true })[1],
    "action-1",
  );
  await userEvent.selectOptions(
    screen.getAllByLabelText("Action reading", { exact: true })[1],
    "main",
  );
  expect(screen.queryByLabelText("Measurement type")).not.toBeInTheDocument();
  await userEvent.type(screen.getAllByLabelText(/^Unit/)[0], "m³");
  await userEvent.type(screen.getAllByLabelText(/^Unit/)[1], "°C");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Settings saved. Device is inactive.");
  expect(devices[0].enabled).toBe(false);
  expect(devices[0].active).toEqual(devices[0].draft);
  expect(devices[0].draft.sensors.map((s) => s.action_id)).toEqual([
    "action-0",
    "action-1",
  ]);
  expect(
    screen.queryByRole("button", { name: "Activate device" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Deactivate" }),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByLabelText("Active", { exact: true }));
  expect(devices[0].enabled).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Read now" })).toBeEnabled(),
  );
  await userEvent.click(screen.getByRole("button", { name: "Read now" }));
  await screen.findByText("123 m³");
  expect(screen.getByLabelText("Measurement log")).toHaveTextContent(
    "Measurement finished: ready.",
  );
  await userEvent.type(screen.getByLabelText("Device name"), " updated");
  expect(devices[0].active?.name).toBe("Water meter");
  expect(screen.getByRole("button", { name: "Read now" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText(
    "Settings saved. Device is active; the next measurement is scheduled.",
  );
  expect(devices[0].active?.name).toBe("Water meter updated");
  expect(screen.getByRole("button", { name: "Read now" })).toBeEnabled();
  await userEvent.click(screen.getByLabelText("Active", { exact: true }));
  expect(devices[0].enabled).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Settings saved. Device is inactive.");
  await waitFor(() => expect(devices[0].enabled).toBe(false));
  await userEvent.click(screen.getByRole("button", { name: "← Devices" }));
  await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await userEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Delete device",
    }),
  );
  await waitFor(() => expect(devices).toHaveLength(0));
  expect(
    requests.every((path) =>
      ["devices", "actions", "device", "device-read", "delete-device"].includes(
        path,
      ),
    ),
  ).toBe(true);
});

it("displays text readings without losing leading zeros", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({
        devices: [
          {
            id: "text",
            draft: { name: "Serial device" },
            active: {
              sensors: [{ id: "serial", name: "Serial Number", unit: "" }],
            },
            status: "ready",
            values: { serial: { value: "000123-AB", status: "ready" } },
          },
        ],
      }),
    })),
  );
  render(<App />);
  expect(
    await screen.findByText("Serial Number: 000123-AB"),
  ).toBeInTheDocument();
});

it("keeps errors visible on collapsed sensors and reveals invalid advanced fields before saving", async () => {
  const { DeviceEditor } = await import("./DevicesPage");
  const draft = {
    name: "Fixture",
    interval_minutes: 30,
    publish: false,
    sensors: [
      {
        id: "serial",
        name: "Serial number",
        unit: "",
        action_id: "reader",
        reading: "serial",
        device_class: "",
        cumulative: false,
        max_change: null,
      },
    ],
  };
  const mutations: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (target: URL, options: RequestInit) => {
      if (options.method === "POST") mutations.push(String(target));
      return {
        ok: true,
        json: async () =>
          new URL(target).pathname.endsWith("/actions")
            ? {
                actions: [
                  {
                    definition: { id: "reader", name: "Reader" },
                    supported: true,
                    enabled: false,
                  },
                ],
              }
            : {
                id: "fixture",
                draft,
                active: draft,
                enabled: true,
                busy: false,
                status: "error",
                publication: "Not published",
                values: {
                  serial: {
                    value: "000123",
                    timestamp: "2026-10-31T12:00:00Z",
                    error: "Reading failed.",
                  },
                },
              },
      };
    }),
  );
  render(
    <DeviceEditor
      id="fixture"
      back={() => {}}
      onDirty={() => {}}
      onBusy={() => {}}
    />,
  );
  expect(await screen.findByText("Reading failed.")).toBeVisible();
  expect(screen.getByText("000123")).toBeVisible();
  const name = screen.getByLabelText("Sensor name");
  const outer = name.closest("details")!;
  expect(outer.open).toBe(false);
  await userEvent.click(outer.querySelector("summary")!);
  await userEvent.click(screen.getByText("Advanced settings"));
  const sensorId = screen.getByLabelText(/^Sensor ID/);
  const advanced = sensorId.closest("details")!;
  await userEvent.clear(sensorId);
  await userEvent.type(sensorId, "INVALID");
  await userEvent.click(advanced.querySelector("summary")!);
  await userEvent.click(outer.querySelector("summary")!);
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(outer.open).toBe(true));
  expect(advanced.open).toBe(true);
  expect(sensorId).toBeVisible();
  expect(mutations).toEqual([]);
  await userEvent.clear(sensorId);
  await userEvent.type(sensorId, "serial");
  const deviceClass = screen.getByLabelText(/^Home Assistant device class/);
  await userEvent.type(deviceClass, "Invalid Class");
  await userEvent.click(advanced.querySelector("summary")!);
  await userEvent.click(outer.querySelector("summary")!);
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(outer.open).toBe(true));
  expect(advanced.open).toBe(true);
  expect(deviceClass).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(mutations).toEqual([]);
});

it("refreshes an untouched Active checkbox and preserves local edits after a failed Save", async () => {
  const { DeviceEditor } = await import("./DevicesPage");
  const draft = {
    name: "Fixture",
    interval_minutes: 30,
    publish: false,
    sensors: [
      {
        id: "serial",
        name: "Serial",
        unit: "",
        action_id: "reader",
        reading: "serial",
        device_class: "",
        cumulative: false,
        max_change: null,
      },
    ],
  };
  let persistedEnabled = true;
  const onDirty = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (target: URL, options: RequestInit) => {
      if (options.method === "POST")
        return {
          ok: false,
          json: async () => ({ error: "Settings could not be saved." }),
        };
      return {
        ok: true,
        json: async () =>
          new URL(target).pathname.endsWith("/actions")
            ? {
                actions: [
                  {
                    definition: { id: "reader", name: "Reader" },
                    supported: true,
                    enabled: false,
                  },
                ],
              }
            : {
                id: "fixture",
                draft,
                active: draft,
                enabled: persistedEnabled,
                busy: false,
                status: persistedEnabled ? "ready" : "disabled",
                publication: "Not published",
                values: {},
              },
      };
    }),
  );
  render(
    <DeviceEditor
      id="fixture"
      back={() => {}}
      onDirty={onDirty}
      onBusy={() => {}}
    />,
  );
  const active = await screen.findByLabelText("Active", { exact: true });
  expect(active).toBeChecked();
  persistedEnabled = false;
  await waitFor(() => expect(active).not.toBeChecked(), { timeout: 3500 });
  expect(onDirty).toHaveBeenLastCalledWith(false);
  await userEvent.click(active);
  await userEvent.type(screen.getByLabelText("Device name"), " edited");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Settings could not be saved.")).toBeVisible();
  expect(active).toBeChecked();
  expect(screen.getByLabelText("Device name")).toHaveValue("Fixture edited");
  expect(screen.getByText("disabled", { exact: true })).toBeVisible();
  expect(screen.getByRole("button", { name: "Read now" })).toBeDisabled();
  expect(onDirty).toHaveBeenLastCalledWith(true);
  expect(persistedEnabled).toBe(false);
});

it("configures a derived rate without another Action and hides stale values", async () => {
  const { DeviceEditor } = await import("./DevicesPage");
  const source = {
    id: "water",
    name: "Water meter",
    unit: "m³",
    action_id: "reader",
    reading: "main",
    device_class: "water",
    cumulative: true,
    max_change: null,
  };
  let state = {
    id: "fixture",
    draft: {
      name: "Water",
      interval_minutes: 30,
      publish: false,
      sensors: [source] as import("../lib/types").DeviceSensor[],
    },
    active: null as import("../lib/types").DeviceConfig | null,
    enabled: false,
    busy: false,
    status: "disabled",
    publication: "Not published",
    values: {
      "measurement-1": {
        value: 99,
        status: "unavailable",
        error: "Waiting for a new valid source measurement.",
      },
    },
  };
  const saves: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (target: URL, options: RequestInit) => {
      if (options.body) {
        const body = JSON.parse(String(options.body));
        saves.push(body);
        state = {
          ...state,
          draft: body.config,
          active: body.config,
          enabled: body.enabled,
        };
      }
      return {
        ok: true,
        json: async () =>
          structuredClone(
            new URL(target).pathname.endsWith("/actions")
              ? {
                  actions: [
                    {
                      definition: { id: "reader", name: "Reader" },
                      supported: true,
                      enabled: false,
                    },
                  ],
                }
              : state,
          ),
      };
    }),
  );
  render(
    <DeviceEditor
      id="fixture"
      back={() => {}}
      onDirty={() => {}}
      onBusy={() => {}}
    />,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Add sensor" }),
  );
  await userEvent.selectOptions(
    screen.getAllByLabelText("Sensor type")[1],
    "rate",
  );
  const selector = screen.getByLabelText("Source sensor");
  expect(within(selector).getAllByRole("option")).toHaveLength(2);
  await userEvent.selectOptions(selector, "water");
  const factor = screen.getByLabelText(/^Conversion factor/);
  await userEvent.clear(factor);
  await userEvent.type(factor, "1000");
  await userEvent.type(screen.getAllByLabelText(/^Unit/)[1], "L/min");
  expect(screen.getAllByLabelText("Action", { exact: true })).toHaveLength(1);
  expect(screen.getByText("Unavailable", { exact: true })).toBeVisible();
  expect(screen.queryByText("99 L/min")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(saves).toHaveLength(1));
  expect(state.draft.sensors[1]).toMatchObject({
    action_id: "",
    reading: "",
    cumulative: false,
    unit: "L/min",
    rate: { source: "water", factor: 1000, time_base: "minute" },
  });
});
