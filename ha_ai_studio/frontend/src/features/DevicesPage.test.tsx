import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect, vi } from "vitest";
import { App } from "../App";
import type { DeviceState } from "../lib/types";

it("creates a device, assigns independent actions, saves drafts, activates, reads and deactivates without browser MQTT", async () => {
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
          if (body.activate) {
            devices[0].active = structuredClone(body.config);
            devices[0].enabled = true;
            devices[0].status = "ready";
          }
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
  await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await screen.findByText("Draft saved. Active measurements are unchanged.");
  expect(devices[0].active).toBeNull();
  expect(devices[0].draft.sensors.map((s) => s.action_id)).toEqual([
    "action-0",
    "action-1",
  ]);
  await userEvent.click(
    screen.getByRole("button", { name: "Activate device" }),
  );
  await userEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Activate device",
    }),
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Read now" })).toBeEnabled(),
  );
  await userEvent.click(screen.getByRole("button", { name: "Read now" }));
  await screen.findByText("123 m³");
  expect(screen.getByLabelText("Measurement log")).toHaveTextContent(
    "Measurement finished: ready.",
  );
  await userEvent.type(screen.getByLabelText("Device name"), " draft");
  await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await screen.findByText("Draft saved. Active measurements are unchanged.");
  expect(screen.getByRole("button", { name: "Read now" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Deactivate" }));
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
      [
        "devices",
        "actions",
        "device",
        "device-read",
        "device-deactivate",
        "delete-device",
      ].includes(path),
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
  await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
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
  await userEvent.click(
    screen.getByRole("button", { name: "Activate device" }),
  );
  await waitFor(() => expect(outer.open).toBe(true));
  expect(advanced.open).toBe(true);
  expect(deviceClass).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(mutations).toEqual([]);
});
