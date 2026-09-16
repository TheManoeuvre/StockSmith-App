import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../api/client", async () => (await import("../test/fakeBackend")).clientMock());
vi.mock("../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
}));

const { setRoutes } = await import("../test/fakeBackend");
const { routeTree } = await import("../routeTree.gen");

async function renderShell() {
  setRoutes([
    { method: "GET" as const, path: "/system/status", respond: () => ({ status: "ok" }) },
    {
      method: "GET" as const,
      path: "/dashboard/summary",
      respond: () => ({
        total_inventory_value: "0",
        active_product_count: 142,
        low_stock_materials: [{ id: 1 }, { id: 2 }, { id: 3 }],
        lowest_buildable_products: [],
        margin_alerts: [],
        orders_awaiting_inventory: [{ line_id: 1, has_bom: true }],
        orders_awaiting_packaging: [],
        items_due_for_count: [],
        items_due_for_count_total: 0,
        unresolved_variance_count: 0,
        open_stock_take: null,
      }),
    },
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ]);
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: ["/orders"] }) });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  return within(await screen.findByRole("navigation"));
}

beforeEach(() => setRoutes([]));

it("orders the nav by the daily loop, grouped under Sell and Stock", async () => {
  const nav = await renderShell();
  const labels = nav.getAllByRole("link").map((a) => a.textContent?.replace(/\d+$/, ""));
  expect(labels).toEqual(["Dashboard", "Orders", "Products", "Materials", "Purchases", "Stock Take"]);
  expect(nav.getByText("Sell")).toBeInTheDocument();
  expect(nav.getByText("Stock")).toBeInTheDocument();
});

it("badges only the rows that want doing — never Dashboard or Products", async () => {
  const nav = await renderShell();
  expect(await nav.findByText("3", { selector: "a span" })).toBeInTheDocument(); // Materials
  expect(nav.getByRole("link", { name: /Orders/ })).toHaveTextContent("1");
  expect(nav.getByRole("link", { name: /Dashboard/ })).toHaveTextContent(/^Dashboard$/);
  expect(nav.getByRole("link", { name: /Products/ })).toHaveTextContent(/^Products$/);
});
