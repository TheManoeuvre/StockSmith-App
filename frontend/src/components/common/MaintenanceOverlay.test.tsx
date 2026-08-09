import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());

const routerState = { pathname: "/products" };
vi.mock("@tanstack/react-router", () => ({
  useRouterState: ({ select }: { select: (s: unknown) => unknown }) =>
    select({ location: { pathname: routerState.pathname } }),
}));

const tauriSettings: { backendUrl?: string } = { backendUrl: "http://127.0.0.1:8000" };
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve(tauriSettings),
}));

const { setRoutes } = await import("../../test/fakeBackend");
const { MaintenanceOverlay } = await import("./MaintenanceOverlay");

function status(overrides: Record<string, unknown> = {}) {
  return {
    status: "ok",
    phase: null,
    app_version: "0.6.0",
    alembic_revision: "c4e8f21a7b93",
    data_fingerprint: "lineage-a:",
    last_restore: null,
    ...overrides,
  };
}

/** /system/status responses served in order, so a poll sequence can be scripted. */
function scriptStatuses(sequence: Record<string, unknown>[]) {
  let index = 0;
  setRoutes([
    {
      method: "GET" as const,
      path: "/system/status",
      respond: () => {
        const value = sequence[Math.min(index, sequence.length - 1)];
        index += 1;
        if (value instanceof Error) throw value;
        return value;
      },
    },
  ]);
}

function renderOverlay() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const clearSpy = vi.spyOn(client, "clear");
  const invalidateSpy = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <MaintenanceOverlay />
    </QueryClientProvider>
  );
  return { clearSpy, invalidateSpy };
}

describe("MaintenanceOverlay", () => {
  beforeEach(() => {
    vi.useRealTimers();
    routerState.pathname = "/products";
    tauriSettings.backendUrl = "http://127.0.0.1:8000";
    setRoutes([{ method: "GET" as const, path: "/system/status", respond: () => status() }]);
  });

  describe("never locking the user out", () => {
    it("stays hidden when no backend has been configured yet", async () => {
      // A first-run thin client hasn't been told where the host is. That's setup, not an outage
      // — and an overlay here would cover the Connection tab needed to fix it.
      tauriSettings.backendUrl = undefined;
      setRoutes([]);
      renderOverlay();

      await new Promise((resolve) => setTimeout(resolve, 300));
      expect(screen.queryByText("Reconnecting")).not.toBeInTheDocument();
    });

    it("never covers Settings", async () => {
      // Settings holds both the connection fields and the Cancel-restore button. Covering it
      // would turn a recoverable problem into one with no way out from inside the app.
      routerState.pathname = "/settings";
      setRoutes([]);
      renderOverlay();

      await new Promise((resolve) => setTimeout(resolve, 300));
      expect(screen.queryByText("Reconnecting")).not.toBeInTheDocument();
    });
  });

  it("stays out of the way when everything is fine", async () => {
    renderOverlay();
    await waitFor(() => expect(screen.queryByText(/Restoring a backup/)).not.toBeInTheDocument());
  });

  it("covers the app while the host is restoring", async () => {
    setRoutes([
      {
        method: "GET" as const,
        path: "/system/status",
        respond: () => status({ status: "maintenance", phase: "restore_staged" }),
      },
    ]);
    renderOverlay();

    expect(await screen.findByText("Restoring a backup")).toBeInTheDocument();
    expect(screen.getByText(/reconnect on its own/)).toBeInTheDocument();
  });

  it("says it is reconnecting when the backend can't be reached at all", async () => {
    setRoutes([]); // no route — the fake backend rejects, as an unreachable host would
    renderOverlay();

    expect(await screen.findByText("Reconnecting")).toBeInTheDocument();
  });

  it("throws the cache away when the database underneath has changed", async () => {
    // The case this exists for: a restore completed, so every cached query describes a database
    // that no longer exists. Invalidating would leave stale numbers on screen until each refetch
    // landed — and after restoring last week's backup those numbers were never true.
    scriptStatuses([
      status({ data_fingerprint: "lineage-a:" }),
      status({ data_fingerprint: "lineage-b:2026-08-09T12:00:00Z" }),
    ]);
    const { clearSpy } = renderOverlay();

    await waitFor(() => expect(clearSpy).toHaveBeenCalled(), { timeout: 8000 });
  });

  it("leaves the cache alone while the fingerprint is unchanged", async () => {
    scriptStatuses([status(), status(), status()]);
    const { clearSpy } = renderOverlay();

    // Long enough for several polls at the 3s interval.
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(clearSpy).not.toHaveBeenCalled();
  });
});
