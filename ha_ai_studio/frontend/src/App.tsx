import { useEffect, useRef, useState } from "react";
import { Button, SidePanel } from "./design-system";
import { DevicesPage, DeviceEditor } from "./features/DevicesPage";
import { ActionsPage } from "./features/ActionsPage";
import { ActionEditor } from "./features/ActionEditor";
import { LivePage } from "./features/LivePage";
import { SettingsPage } from "./features/SettingsPage";
type Route =
  | { page: "devices" }
  | { page: "device"; id: string }
  | { page: "actions" }
  | { page: "settings" }
  | { page: "editor"; id: string }
  | { page: "live"; id: string };
function readRoute(): Route {
  const [page, id, child] = location.hash.split("/");
  if (page === "#devices" && id) {
    try {
      return { page: "device", id: decodeURIComponent(id) };
    } catch {
      return { page: "devices" };
    }
  }
  if (page === "#actions" && !id) return { page: "actions" };
  if (page === "#settings") return { page: "settings" };
  if (page === "#actions" && id) {
    try {
      return {
        page: child === "live" ? "live" : "editor",
        id: decodeURIComponent(id),
      };
    } catch {
      /* Invalid links fall back to the table. */
    }
  }
  return { page: "devices" };
}
function hash(route: Route) {
  if (route.page === "devices") return "#devices";
  if (route.page === "device")
    return `#devices/${encodeURIComponent(route.id)}`;
  return route.page === "settings"
    ? "#settings"
    : route.page === "actions"
      ? "#actions"
      : `#actions/${encodeURIComponent(route.id)}${route.page === "live" ? "/live" : ""}`;
}
export function App() {
  const [route, setRoute] = useState<Route>(readRoute);
  const [pending, setPending] = useState<Route>();
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const current = useRef({ route, dirty, busy });
  current.current = { route, dirty, busy };
  function move(next: Route) {
    setDirty(false);
    setRoute(next);
    history.replaceState(null, "", hash(next));
  }
  function navigate(next: Route) {
    if (busy) return;
    if (dirty) setPending(next);
    else move(next);
  }
  useEffect(() => {
    const unload = (e: BeforeUnloadEvent) => {
      if (current.current.dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    const change = () => {
      const next = readRoute();
      if (current.current.dirty || current.current.busy) {
        history.replaceState(null, "", hash(current.current.route));
        if (!current.current.busy) setPending(next);
      } else move(next);
    };
    window.addEventListener("beforeunload", unload);
    window.addEventListener("hashchange", change);
    return () => {
      window.removeEventListener("beforeunload", unload);
      window.removeEventListener("hashchange", change);
    };
  }, []);
  return (
    <>
      <header className="app-header">
        <a
          className="brand"
          href="#devices"
          aria-label="HA AI Studio — Devices"
          onClick={(e) => {
            e.preventDefault();
            navigate({ page: "devices" });
          }}
        >
          <svg className="brand-logo" viewBox="0 0 40 40" aria-hidden="true">
            <path
              d="M5 19 20 6l15 13v15H5Z"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinejoin="round"
            />
            <path
              d="M13 27l7-7 7 7M20 20v-7"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
            <circle cx="13" cy="27" r="3" fill="currentColor" />
            <circle cx="27" cy="27" r="3" fill="currentColor" />
            <circle cx="20" cy="19" r="3" fill="currentColor" />
            <path
              d="m32 3 1.4 3.6L37 8l-3.6 1.4L32 13l-1.4-3.6L27 8l3.6-1.4Z"
              fill="currentColor"
            />
          </svg>
          <strong>HA AI Studio</strong>
        </a>
        <nav aria-label="Main navigation">
          <Button
            disabled={busy}
            aria-current={
              ["devices", "device"].includes(route.page) ? "page" : undefined
            }
            onClick={() => navigate({ page: "devices" })}
          >
            Devices
          </Button>
          <Button
            disabled={busy}
            aria-current={
              ["actions", "editor", "live"].includes(route.page)
                ? "page"
                : undefined
            }
            onClick={() => navigate({ page: "actions" })}
          >
            Actions
          </Button>
          <Button
            disabled={busy}
            aria-current={route.page === "settings" ? "page" : undefined}
            onClick={() => navigate({ page: "settings" })}
          >
            Settings
          </Button>
        </nav>
      </header>
      <main>
        {route.page === "devices" && (
          <DevicesPage open={(id) => navigate({ page: "device", id })} />
        )}
        {route.page === "device" && (
          <DeviceEditor
            key={route.id}
            id={route.id}
            back={() => navigate({ page: "devices" })}
            onDirty={setDirty}
            onBusy={setBusy}
          />
        )}
        {route.page === "actions" && (
          <ActionsPage
            open={(id) => navigate({ page: "editor", id })}
            live={(id) => navigate({ page: "live", id })}
          />
        )}
        {route.page === "settings" && <SettingsPage />}
        {route.page === "editor" && (
          <ActionEditor
            key={route.id}
            id={route.id}
            back={() => navigate({ page: "actions" })}
            onDirty={setDirty}
            onBusy={setBusy}
          />
        )}
        {route.page === "live" && (
          <LivePage
            key={route.id}
            id={route.id}
            back={() => navigate({ page: "actions" })}
          />
        )}
      </main>
      {pending && (
        <SidePanel
          title="Unsaved changes"
          onClose={() => setPending(undefined)}
          footer={
            <>
              <Button onClick={() => setPending(undefined)}>
                Keep editing
              </Button>
              <Button
                onClick={() => {
                  move(pending);
                  setPending(undefined);
                }}
              >
                Discard changes
              </Button>
            </>
          }
        >
          <p>Your configuration changes have not been saved.</p>
        </SidePanel>
      )}
    </>
  );
}
