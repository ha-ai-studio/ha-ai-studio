import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ActionEditor } from "./features/ActionEditor";
import { DraftTest, EnvironmentPanel } from "./features/ActionPanels";
import { readSettings, SchemaEditor } from "./features/SchemaEditor";
import { api, appUrl } from "./lib/api";
import { mqttTestAutomationYaml } from "./lib/ha-example";
import type { ActionState, Configuration, Schema } from "./lib/types";
const config: Configuration = {
  name: "Fixture",
  input: { broker_id: null, topic: "fixture/request" },
  output: { broker_id: null, topic: "fixture/result" },
  settings: {},
};
function action(): ActionState {
  return {
    definition: { id: "fixture", name: "Fixture", type: "git-fixture" },
    supported: true,
    enabled: false,
    busy: false,
    mqtt: "Disabled",
    config: { draft: structuredClone(config), active: null },
    plugin: {
      type: "git-fixture",
      name: "Fixture type",
      dynamic: true,
      version: "1",
      commit: "a".repeat(40),
      example: { data: 0 },
      input_schema: {},
      output_schema: {},
      settings_schema: {
        type: "object",
        additionalProperties: false,
        properties: {},
      },
    },
    environment: { status: "Ready", logs: ["Environment is ready."] },
    issues: [],
  };
}
function mockApi(value = action()) {
  const calls: { path: string; body: unknown; id: string | null }[] = [];
  let operationError = "",
    readError = "";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: URL, options: RequestInit) => {
      const path = url.pathname;
      if (
        ![
          "actions",
          "action",
          "environment",
          "deactivate",
          "reload-source",
          "action-version",
          "brokers",
          "repositories",
          "discovery",
          "delete-action",
          "test",
        ].some((endpoint) => path.endsWith("/api/" + endpoint))
      )
        throw new Error("Unexpected test endpoint: " + path);
      const body = options.body ? JSON.parse(String(options.body)) : undefined;
      calls.push({ path, body, id: url.searchParams.get("id") });
      let data: unknown = value;
      if (path.endsWith("/actions"))
        data = { actions: [value], types: [value.plugin] };
      else if (path.endsWith("/brokers"))
        data = { brokers: [], default_id: null };
      else if (path.endsWith("/repositories")) data = { repositories: [] };
      else if (path.endsWith("/discovery"))
        data = {
          config: {
            enabled: false,
            broker_id: null,
            prefix: "homeassistant",
            ha_status_topic: "homeassistant/status",
          },
          status: "Disabled",
          pending_cleanup: 0,
          legacy_pending: false,
        };
      if (path.endsWith("/test"))
        data = {
          mode: "test",
          delivery: "not_sent",
          result: { data: JSON.parse(body.payload).data },
        };
      if (path.endsWith("/deactivate")) value.enabled = false;
      if (path.endsWith("/action") && body) {
        value.config = {
          draft: body.config,
          active: body.enabled ? body.config : null,
        };
        value.enabled = body.enabled;
      }
      const error = path.endsWith("/environment")
        ? operationError
        : path.endsWith("/action")
          ? readError
          : "";
      return {
        ok: !error,
        json: async () => (error ? { error } : structuredClone(data)),
      };
    }),
  );
  return {
    calls,
    operationError: (text: string) => {
      operationError = text;
    },
    readError: (text: string) => {
      readError = text;
    },
  };
}
describe("schema settings preserve JSON semantics", () => {
  const schema: Schema = {
    type: "object",
    additionalProperties: false,
    properties: {
      limit: { type: "integer", title: "Limit" },
      enabled: { type: "boolean" },
      choice: { type: "string", enum: [null, false, 0, ""] },
    },
  };
  it("preserves absent optional values, zero, false and enum null", () => {
    expect(readSettings(schema, {}, {}, "")).toEqual({});
    expect(readSettings(schema, { limit: 0, enabled: false }, {}, "")).toEqual({
      limit: 0,
      enabled: false,
    });
    expect(readSettings(schema, { limit: 7 }, { limit: "" }, "")).toEqual({});
    expect(
      readSettings(schema, {}, { limit: "0", enabled: false, choice: "0" }, ""),
    ).toEqual({ limit: 0, enabled: false, choice: null });
    expect(readSettings(schema, {}, { choice: "" }, "")).toEqual({});
  });
  it("rejects required, fractional and nonfinite numbers", () => {
    for (const value of ["1.2", "Infinity", "invalid"])
      expect(() => readSettings(schema, {}, { limit: value }, "")).toThrow(
        /valid integer/,
      );
    expect(() =>
      readSettings({ ...schema, required: ["limit"] }, {}, {}, ""),
    ).toThrow(/required/);
  });
  it.each(["null", "false", "0", "[]", "{}", '"text"'])(
    "preserves raw JSON %s",
    (raw) => expect(readSettings({}, {}, {}, raw)).toEqual(JSON.parse(raw)),
  );
});
it("HA YAML requires an example and preserves null/false/zero", () => {
  for (const value of [null, undefined, {}, [], { request_id: "x" }])
    expect(() => mqttTestAutomationYaml(config, value)).toThrow(
      /No valid example/,
    );
  for (const data of [null, false, 0])
    expect(mqttTestAutomationYaml(config, { data })).toContain(
      JSON.stringify(JSON.stringify({ data })),
    );
});
it("uses relative Ingress URLs and mutation protection headers", async () => {
  history.replaceState(null, "", "/api/hassio_ingress/fixture/");
  mockApi();
  await api("action", { config, enabled: false }, "a/b");
  expect(vi.mocked(fetch).mock.calls[0][0].toString()).toBe(
    "http://localhost:3000/api/hassio_ingress/fixture/api/action?id=a%2Fb",
  );
  expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toMatchObject({
    "X-HA-AI-Studio-Request": "1",
    "Content-Type": "application/json",
  });
  expect(appUrl("action-types/echo/editor.js").pathname).toBe(
    "/api/hassio_ingress/fixture/action-types/echo/editor.js",
  );
});
it("rebuild failures stay in the panel through successful status refresh and recover on retry", async () => {
  const mock = mockApi();
  mock.operationError("Environment rebuild failed.");
  render(<EnvironmentPanel initial={action()} close={() => {}} />);
  await userEvent.click(
    screen.getByRole("button", { name: "Rebuild environment" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Environment rebuild failed.",
  );
  await new Promise((resolve) => setTimeout(resolve, 2100));
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Environment rebuild failed.",
  );
  expect(mock.calls.at(-1)?.id).toBe("fixture");
  mock.operationError("");
  await userEvent.click(
    screen.getByRole("button", { name: "Rebuild environment" }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
  );
});
it("manual status errors appear in the installation panel and recover", async () => {
  const mock = mockApi();
  mock.readError("Status unavailable.");
  render(<EnvironmentPanel initial={action()} close={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "Refresh status" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Status unavailable.",
  );
  mock.readError("");
  await userEvent.click(screen.getByRole("button", { name: "Refresh status" }));
  await waitFor(() =>
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
  );
});
it("missing examples disable HA template while zero-valued examples work", async () => {
  const value = action();
  value.plugin!.example = null;
  mockApi(value);
  const view = render(
    <ActionEditor
      id="fixture"
      back={() => {}}
      onDirty={() => {}}
      onBusy={() => {}}
    />,
  );
  expect(
    await screen.findByRole("button", { name: "Test in Home Assistant" }),
  ).toBeDisabled();
  view.unmount();
  value.plugin!.example = { data: 0 };
  mockApi(value);
  render(
    <ActionEditor
      id="fixture"
      back={() => {}}
      onDirty={() => {}}
      onBusy={() => {}}
    />,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Test in Home Assistant" }),
  );
  expect(await screen.findByRole("dialog")).toHaveTextContent("Draft example.");
  expect(
    (
      screen.getByRole("textbox", {
        name: "Automation YAML",
      }) as HTMLTextAreaElement
    ).value,
  ).toContain("mqtt.publish");
});
it("create, edit, dirty guard, activate, deactivate and delete use the real React handlers with mocked transport", async () => {
  const mock = mockApi();
  history.replaceState(null, "", "#actions");
  render(<App />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Create action" }),
  );
  const panel = screen.getByRole("dialog");
  await userEvent.type(
    within(panel).getByRole("textbox", { name: "Name" }),
    "Created action",
  );
  await userEvent.click(
    within(panel).getByRole("button", { name: "Create action" }),
  );
  const name = await screen.findByRole("textbox", { name: "Name" });
  fireEvent.change(name, { target: { value: "Updated action" } });
  await userEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(await screen.findByRole("dialog")).toHaveTextContent(
    "Unsaved changes",
  );
  await userEvent.click(screen.getByRole("button", { name: "Keep editing" }));
  expect(name).toHaveValue("Updated action");
  await userEvent.click(screen.getByRole("checkbox", { name: "Active" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Action saved. Active.");
  expect(
    mock.calls.find((c) => c.path.endsWith("/action") && c.body)?.body,
  ).toMatchObject({ enabled: true, config: { name: "Updated action" } });
  await userEvent.click(screen.getByRole("checkbox", { name: "Active" }));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Action saved. Inactive.");
  await userEvent.click(screen.getByRole("button", { name: "← Actions" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "Delete Fixture" }),
  );
  await userEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Delete action",
    }),
  );
  await waitFor(() =>
    expect(mock.calls.some((c) => c.path.endsWith("/delete-action"))).toBe(
      true,
    ),
  );
});

it("preserved unsupported legacy instances remain inspectable and disabled", async () => {
  const value = action();
  value.supported = false;
  value.plugin = null;
  value.config = null;
  value.issues = [
    "Unsupported legacy workflow. Disabled; original files and IDs are preserved.",
  ];
  mockApi(value);
  render(
    <ActionEditor
      id="fixture"
      back={() => {}}
      onDirty={() => {}}
      onBusy={() => {}}
    />,
  );
  expect(
    await screen.findByText("Unsupported legacy action · Disabled"),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/original files and IDs are preserved/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Save" }),
  ).not.toBeInTheDocument();
});

it("controlled numeric editor emits absence when cleared and preserves zero", async () => {
  const schema: Schema = {
    type: "object",
    additionalProperties: false,
    properties: { limit: { type: "integer", title: "Limit" } },
  };
  const onValues = vi.fn();
  render(
    <SchemaEditor
      schema={schema}
      initial={{ limit: 7 }}
      values={{}}
      raw="{}"
      onValues={onValues}
      onRaw={() => {}}
    />,
  );
  await userEvent.clear(screen.getByRole("spinbutton", { name: "Limit" }));
  expect(
    readSettings(schema, { limit: 7 }, onValues.mock.calls.at(-1)![0], ""),
  ).toEqual({});
  await userEvent.type(screen.getByRole("spinbutton", { name: "Limit" }), "0");
  expect(onValues).toHaveBeenCalled();
});
it("compatibility draft preview only uses the test API and preserves null", async () => {
  const mock = mockApi();
  render(<DraftTest id="fixture" config={config} close={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "Run draft test" }));
  expect(await screen.findByLabelText("Draft test result")).toHaveTextContent(
    "not_sent",
  );
  expect(mock.calls).toHaveLength(1);
  expect(mock.calls[0].path).toBe("/api/test");
  expect(mock.calls[0].body).toMatchObject({
    config,
    payload: '{"request_id":"draft-test","data":null}',
  });
});

describe("Local custom editors", () => {
  it("can deactivate an active action even when its editor fails", async () => {
    const value = action();
    value.enabled = true;
    value.plugin!.local = true;
    value.plugin!.editor = "plugin-assets/fixture/custom/ui/missing.js";
    const mock = mockApi(value);
    render(
      <ActionEditor
        id="fixture"
        back={() => {}}
        onDirty={() => {}}
        onBusy={() => {}}
      />,
    );
    await screen.findByText(
      "Action editor failed to load. Check its module and render contract.",
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Deactivate" }));
    await waitFor(() =>
      expect(mock.calls.some((c) => c.path.endsWith("/api/deactivate"))).toBe(
        true,
      ),
    );
  });
  it("blocks reload with unsaved changes and exposes it only for local sources", async () => {
    const value = action();
    value.plugin!.local = true;
    mockApi(value);
    render(
      <ActionEditor
        id="fixture"
        back={() => {}}
        onDirty={() => {}}
        onBusy={() => {}}
      />,
    );
    const reload = await screen.findByRole("button", {
      name: "Reload from source",
    });
    expect(reload).toBeEnabled();
    await userEvent.type(screen.getByLabelText("Name"), " changed");
    expect(reload).toBeDisabled();
  });
});

it("offers stable release sources without local development and submits latest", async () => {
  const transport = mockApi();
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "Settings" }));
  await user.click(
    await screen.findByRole("button", { name: "Add repository" }),
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Source type" }),
    "release",
  );
  expect(
    screen.queryByRole("option", { name: "Local directory" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("textbox", { name: /Release tag or latest/ }),
  ).toHaveValue("latest");
  await user.type(
    screen.getByRole("textbox", { name: "GitHub repository URL" }),
    "https://github.com/example/actions",
  );
  await user.click(
    screen
      .getAllByRole("button", { name: "Add repository" })
      .find((button) => button.getAttribute("form") === "repository-form")!,
  );
  await waitFor(() =>
    expect(
      transport.calls.some(
        (c) =>
          c.path.endsWith("/repositories") &&
          JSON.stringify(c.body) ===
            JSON.stringify({
              kind: "release",
              url: "https://github.com/example/actions",
              ref: "latest",
            }),
      ),
    ).toBe(true),
  );
});

describe("Action version selection", () => {
  function versioned() {
    const value = action();
    value.plugin!.snapshot = "new";
    value.plugin!.version = "2";
    value.versions = [
      {
        snapshot: "old",
        version: "1",
        commit: "a".repeat(40),
        release: { tag: "v1" },
        url: "https://github.com/test/repo/releases/tag/v1",
      },
      {
        snapshot: "new",
        version: "2",
        commit: "b".repeat(40),
        release: { tag: "v2" },
      },
    ];
    return value;
  }
  it("selects a downgrade in General and applies its immutable snapshot", async () => {
    const value = versioned();
    const fixture = mockApi(value);
    render(
      <ActionEditor
        id="fixture"
        back={() => {}}
        onDirty={() => {}}
        onBusy={() => {}}
      />,
    );
    const select = await screen.findByRole("combobox", { name: "Version" });
    expect(select).toHaveValue("new");
    await userEvent.selectOptions(select, "old");
    expect(
      screen.getByRole("link", { name: "View release or commit" }),
    ).toHaveAttribute("href", value.versions![0].url);
    await userEvent.click(
      screen.getByRole("button", { name: "Apply version" }),
    );
    await waitFor(() =>
      expect(
        fixture.calls.some(
          (c) =>
            c.path.endsWith("/action-version") &&
            (c.body as { snapshot: string }).snapshot === "old",
        ),
      ).toBe(true),
    );
  });
  it("prevents version changes while settings have unsaved changes", async () => {
    mockApi(versioned());
    render(
      <ActionEditor
        id="fixture"
        back={() => {}}
        onDirty={() => {}}
        onBusy={() => {}}
      />,
    );
    const select = await screen.findByRole("combobox", { name: "Version" });
    await userEvent.selectOptions(select, "old");
    await userEvent.type(
      screen.getByRole("textbox", { name: "Name" }),
      " edited",
    );
    expect(select).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Apply version" }),
    ).toBeDisabled();
  });
});
