import { QueryClient as QC, QueryClientProvider as QCP } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRouter,
} from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
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

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { routeTree } = await import("../../routeTree.gen");

function line(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    purchase_id: 1,
    material_id: 7,
    qty: "1000",
    total_cost: "20.00",
    notes: null,
    closed_at: null,
    received_qty: "0",
    outstanding_qty: "1000",
    receipts: [],
    ...over,
  };
}

function purchase(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    supplier_id: 3,
    supplier_name: "Polymax",
    order_date: "2026-08-21",
    expected_arrival_date: "2026-08-27",
    status: "ordered",
    received_at: null,
    notes: null,
    created_at: "2026-08-21",
    updated_at: "2026-08-21",
    lines: [line()],
    ...over,
  };
}

function routes(items: unknown[]) {
  return [
    { method: "GET" as const, path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET" as const, path: "/materials", respond: () => [{ id: 7, name: "PLA Black" }] },
    {
      method: "POST" as const,
      path: /^\/purchases\/\d+\/receive$/,
      respond: () => purchase({ status: "received" }),
    },
    { method: "GET" as const, path: /^\/purchases(\?|$)/, respond: () => items },
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ];
}

async function renderList() {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ["/purchases"] }),
  });
  render(
    <QCP client={new QC({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QCP>,
  );
  await screen.findByRole("heading", { name: "Purchases" }, { timeout: 5000 });
  return router;
}

beforeEach(() =>
  setRoutes(
    routes([
      purchase(),
      purchase({ id: 2, supplier_name: "Kite Packaging", status: "received", lines: [line({ id: 2, purchase_id: 2, received_qty: "1000", outstanding_qty: "0" })] }),
    ]),
  ),
);

it("filters the list by status tab", async () => {
  const user = userEvent.setup();
  await renderList();

  // Both rows present under "All purchases".
  expect(screen.getByText("#1")).toBeInTheDocument();
  expect(screen.getByText("#2")).toBeInTheDocument();

  await user.click(await screen.findByRole("button", { name: /Received/ }));

  await waitFor(() => expect(screen.queryByText("#1")).not.toBeInTheDocument());
  expect(screen.getByText("#2")).toBeInTheDocument();
});

it("receives an ordered purchase from the row action", async () => {
  const user = userEvent.setup();
  await renderList();

  const row = screen.getByText("#1").closest("tr")!;
  await user.click(within(row).getByRole("button", { name: "Receive all" }));

  await waitFor(() =>
    expect(
      calls.some((c) => c.method === "POST" && c.path === "/purchases/1/receive"),
    ).toBe(true),
  );
});

it("opens the slide-over on a row click", async () => {
  const user = userEvent.setup();
  const router = await renderList();

  await user.click(screen.getByText("Polymax"));

  await waitFor(() =>
    expect(router.state.location.pathname).toBe("/purchases/1"),
  );
});
