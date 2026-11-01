import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ImportDeviceTemplate, exportDeviceTemplate } from "./DeviceTemplates";

const template = {
  format: "ha-ai-studio-device-template",
  version: 1,
  config: {
    name: "Meter",
    interval_minutes: 30,
    publish: false,
    sensors: [{ id: "main", name: "Reading", action_id: "action-1" }],
    functions: [
      {
        id: "run",
        name: "Read now",
        action_id: "action-1",
        function: "execute",
      },
    ],
  },
};

it("previews a template and requires explicit compatible Action assignments before importing", async () => {
  const calls: { endpoint: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, options: RequestInit) => {
      const endpoint = url.pathname.split("/").at(-1)!;
      const body = options.body ? JSON.parse(String(options.body)) : undefined;
      calls.push({ endpoint, body });
      return {
        ok: true,
        json: async () =>
          endpoint === "actions"
            ? {
                actions: [
                  {
                    definition: { id: "reader", name: "Reader" },
                    supported: true,
                    plugin: { functions: ["execute"] },
                  },
                  {
                    definition: { id: "other", name: "Unsupported" },
                    supported: true,
                    plugin: {},
                  },
                ],
              }
            : endpoint === "device-template-preview"
              ? template
              : { id: "new-device" },
      };
    }),
  );
  const open = vi.fn();
  const close = vi.fn();
  const user = userEvent.setup();
  render(<ImportDeviceTemplate open={open} close={close} />);
  await waitFor(() =>
    expect(screen.getByLabelText("Template file")).toBeEnabled(),
  );
  const file = new File([JSON.stringify(template)], "meter.json", {
    type: "application/json",
  });
  Object.defineProperty(file, "text", {
    value: async () => JSON.stringify(template),
  });
  await user.upload(screen.getByLabelText("Template file"), file);
  expect(await screen.findByLabelText("Device name")).toHaveValue("Meter");
  expect(screen.getByRole("button", { name: "Import Device" })).toBeDisabled();
  expect(
    screen.queryByRole("option", { name: "Unsupported" }),
  ).not.toBeInTheDocument();
  await user.selectOptions(screen.getByRole("combobox"), "reader");
  await user.click(screen.getByRole("button", { name: "Import Device" }));
  await waitFor(() => expect(open).toHaveBeenCalledWith("new-device"));
  expect(close).toHaveBeenCalled();
  expect(calls.at(-1)).toEqual({
    endpoint: "device-template-import",
    body: { template, name: "Meter", actions: { "action-1": "reader" } },
  });
  expect(
    calls.some((call) =>
      ["device-read", "device-function", "device"].includes(call.endpoint),
    ),
  ).toBe(false);
});

it("downloads the saved template as JSON", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => template })),
  );
  const create = vi.fn(() => "blob:template");
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = create;
      static revokeObjectURL = vi.fn();
    },
  );
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  await exportDeviceTemplate("device-id");
  expect(create).toHaveBeenCalledWith(expect.any(Blob));
  expect(click).toHaveBeenCalled();
  click.mockRestore();
});

it("creates from plugin templates and clears assignments when changing templates", async () => {
  const calls: { endpoint: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, options: RequestInit) => {
      const endpoint = url.pathname.split("/").at(-1)!;
      calls.push({
        endpoint,
        body: options.body ? JSON.parse(String(options.body)) : undefined,
      });
      return {
        ok: true,
        json: async () =>
          endpoint === "actions"
            ? {
                actions: [
                  {
                    definition: { id: "reader", name: "Reader" },
                    supported: true,
                    plugin: { functions: ["execute"] },
                  },
                ],
              }
            : endpoint === "device-templates"
              ? {
                  templates: [
                    {
                      id: "one:meter",
                      source: "first-plugin",
                      ref: "main",
                      template,
                    },
                    {
                      id: "two:meter",
                      source: "second-plugin",
                      ref: "main",
                      template,
                    },
                  ],
                }
              : { id: "created" },
      };
    }),
  );
  const open = vi.fn();
  const user = userEvent.setup();
  render(<ImportDeviceTemplate createDevice open={open} close={() => {}} />);
  await screen.findByRole("option", { name: /first-plugin/ });
  expect(screen.queryByLabelText("Template file")).not.toBeInTheDocument();
  await user.selectOptions(
    screen.getByLabelText("Device template"),
    "one:meter",
  );
  expect(screen.getByLabelText("Device name")).toHaveValue("Meter");
  expect(screen.getByRole("button", { name: "Create Device" })).toBeDisabled();
  await user.selectOptions(
    screen.getByLabelText("Action for Reading, Read now"),
    "reader",
  );
  await user.selectOptions(screen.getByLabelText("Device template"), "");
  expect(screen.getByLabelText("Device name")).toHaveValue("Meter");
  expect(
    screen.queryByLabelText("Action for Reading, Read now"),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create Device" })).toBeEnabled();
  await user.selectOptions(
    screen.getByLabelText("Device template"),
    "two:meter",
  );
  expect(screen.getByRole("button", { name: "Create Device" })).toBeDisabled();
  await user.selectOptions(
    screen.getByLabelText("Action for Reading, Read now"),
    "reader",
  );
  await user.click(screen.getByRole("button", { name: "Create Device" }));
  await waitFor(() => expect(open).toHaveBeenCalledWith("created"));
  expect(calls.at(-1)).toEqual({
    endpoint: "device-template-import",
    body: { template, name: "Meter", actions: { "action-1": "reader" } },
  });
});

it("explains how to add plugin templates when the catalog is empty", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({ actions: [], templates: [] }),
    })),
  );
  render(
    <ImportDeviceTemplate createDevice open={() => {}} close={() => {}} />,
  );
  expect(
    await screen.findByText(/No plugin templates available/),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create Device" })).toBeDisabled();
});

it("shows catalog load errors without offering creation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      json: async () => ({ error: "Catalog unavailable" }),
    })),
  );
  render(
    <ImportDeviceTemplate createDevice open={() => {}} close={() => {}} />,
  );
  expect(await screen.findByText("Catalog unavailable")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create Device" })).toBeDisabled();
});
