import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRouter,
} from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../api/client", async () =>
  (await import("../test/fakeBackend")).clientMock(),
);
vi.mock("../lib/tauri", () => ({
  getSettings: () =>
    Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
}));

const { setRoutes } = await import("../test/fakeBackend");
const { routeTree } = await import("../routeTree.gen");

function summary(over: Record<string, unknown> = {}) {
  return {
    total_inventory_value: "1234.50",
    active_product_count: 12,
    low_stock_materials: [
      {
        id: 7,
        name: "PLA Black",
        current_qty: "120",
        reorder_threshold: "500",
        on_order_qty: "0",
        allocated_qty: "0",
        supplier_id: 3,
        supplier_name: "Polymax",
        consumption_rate_per_week: "80",
        weeks_of_supply: "1.5",
        fg_buffer_weeks: "0",
        lead_time_days: 10,
        status: "critical",
      },
    ],
    lowest_buildable_products: [],
    margin_alerts: [
      {
        product_id: 9,
        name: "Widget A",
        previous_margin_percent: "40.0",
        current_margin_percent: "22.0",
      },
    ],
    orders_awaiting_inventory: [
      {
        line_id: 1,
        order_id: 101,
        product_id: 9,
        variant_id: null,
        product_name: "Widget A",
        variant_name: null,
        short_by: 3,
        order_placed_at: "2026-08-20T09:00:00Z",
      },
    ],
    orders_awaiting_packaging: [
      {
        order_id: 102,
        material_id: 15,
        material_name: "Small box",
        short_by: "5",
        order_placed_at: "2026-08-18T09:00:00Z",
      },
    ],
    items_due_for_count: [
      {
        scope: "material",
        material_id: 7,
        product_id: null,
        variant_id: null,
        name: "PLA Black",
        abc_class: "A",
        interval_days: 30,
        last_stock_take_at: "2026-07-01T00:00:00Z",
        days_overdue: 12,
      },
    ],
    items_due_for_count_total: 4,
    unresolved_variance_count: 2,
    open_stock_take: {
      id: 5,
      started_at: "2026-08-22T09:00:00Z",
      open_days: 3,
      line_count: 10,
      counted_count: 4,
    },
    ...over,
  };
}

function routes(data: unknown) {
  return [
    { method: "GET" as const, path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET" as const, path: "/dashboard/summary", respond: () => data },
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ];
}

async function renderDashboard(over: Record<string, unknown> = {}) {
  setRoutes(routes(summary(over)));
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  await screen.findByText("Time to stockout", undefined, { timeout: 5000 });
  return router;
}

beforeEach(() => setRoutes([]));

it("renders the four KPI tiles off the summary", async () => {
  await renderDashboard();
  expect(screen.getAllByText("Blocked orders").length).toBeGreaterThan(0);
  expect(screen.getByText("Materials at risk")).toBeInTheDocument();
  expect(screen.getByText("Overdue counts")).toBeInTheDocument();
  expect(screen.getByText("Inventory value")).toBeInTheDocument();
  expect(screen.getByText("£1234.50")).toBeInTheDocument();
});

it("navigates to Orders when the Blocked orders tile is clicked", async () => {
  const user = userEvent.setup();
  const router = await renderDashboard();

  // The KPI tile label; the first match is the clickable tile.
  await user.click(screen.getAllByText("Blocked orders")[0]);

  await waitFor(() =>
    expect(router.state.location.pathname).toBe("/orders"),
  );
});

it("merges short-stock and short-packaging rows into the Blocked orders table", async () => {
  await renderDashboard();
  expect(await screen.findByText("Short stock")).toBeInTheDocument();
  expect(screen.getByText("Short packaging")).toBeInTheDocument();
  expect(screen.getAllByText("Widget A").length).toBeGreaterThan(0);
  expect(screen.getByText("Small box")).toBeInTheDocument();
  // Oldest-placed first: the short-packaging order (18 Aug) above the short-stock one (20 Aug).
  const labels = screen.getAllByText(/Short (stock|packaging)/);
  expect(labels[0]).toHaveTextContent("Short packaging");
});

it("shows the stock-take progress line and a due-for-count entry", async () => {
  await renderDashboard();
  expect(await screen.findByText(/4 \/ 10 counted/)).toBeInTheDocument();
  expect(screen.getByText("Due for counting")).toBeInTheDocument();
  expect(screen.getByText("12 days over")).toBeInTheDocument();
});

it("hides the Blocked orders table and margin card when their data is empty", async () => {
  await renderDashboard({
    orders_awaiting_inventory: [],
    orders_awaiting_packaging: [],
    margin_alerts: [],
  });
  expect(
    await screen.findByText(/No blocked orders/),
  ).toBeInTheDocument();
  expect(screen.queryByText("Margin moved")).not.toBeInTheDocument();
});
