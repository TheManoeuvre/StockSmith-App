import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRouter,
} from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () =>
  (await import("../../test/fakeBackend")).clientMock(),
);
vi.mock("../../lib/tauri", () => ({
  getSettings: () =>
    Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
}));

const { setRoutes } = await import("../../test/fakeBackend");
const { routeTree } = await import("../../routeTree.gen");

function take(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    status: "open",
    includes_materials: true,
    includes_products: true,
    overdue_only: false,
    scope_description: "Everything",
    started_at: "2026-08-22T09:00:00Z",
    closed_at: null,
    notes: null,
    open_days: 3,
    progress_status: "open",
    line_count: 10,
    counted_count: 4,
    completed_count: 4,
    pending_count: 6,
    conflict_count: 0,
    ...over,
  };
}

function routes(takes: unknown[]) {
  return [
    { method: "GET" as const, path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET" as const, path: "/stock-takes", respond: () => takes },
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ];
}

async function renderList() {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ["/stock-takes"] }),
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", { name: "Stock takes" }, { timeout: 5000 });
  return router;
}

beforeEach(() =>
  setRoutes(
    routes([
      take(),
      take({
        id: 2,
        status: "closed",
        progress_status: "partially_completed",
        closed_at: "2026-06-02T00:00:00Z",
        scope_description: "Filament only",
        line_count: 8,
        counted_count: 0,
        completed_count: 6,
        pending_count: 0,
      }),
    ]),
  ),
);

it("filters takes by the Open / Closed tabs", async () => {
  const user = userEvent.setup();
  await renderList();

  expect(screen.getByText("Everything")).toBeInTheDocument();
  expect(screen.getByText("Filament only")).toBeInTheDocument();

  await user.click(await screen.findByRole("button", { name: /Closed/ }));

  await waitFor(() =>
    expect(screen.queryByText("Everything")).not.toBeInTheDocument(),
  );
  expect(screen.getByText("Filament only")).toBeInTheDocument();
});

it("shows the derived status and keeps closed-take progress non-zero", async () => {
  await renderList();

  const closedRow = screen.getByText("Filament only").closest("tr")!;
  // Progress reflects the rows that were counted, not counted_count (which is 0 once a
  // take closes and its counted lines move on to their outcome).
  expect(closedRow).toHaveTextContent("6 / 8");
  expect(closedRow).toHaveTextContent("Partially completed");

  const openRow = screen.getByText("Everything").closest("tr")!;
  expect(openRow).toHaveTextContent(/Open 3 days/);
});

it("opens a take on a row click", async () => {
  const user = userEvent.setup();
  const router = await renderList();

  await user.click(screen.getByText("Everything"));

  await waitFor(() =>
    expect(router.state.location.pathname).toBe("/stock-takes/1"),
  );
});
