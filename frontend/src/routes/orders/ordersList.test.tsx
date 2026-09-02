import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
    order_id: 900,
    product_id: 5,
    variant_id: null,
    product_name: "Hex Planter",
    variant_name: null,
    sku: "HEX-1",
    ordered_qty: 2,
    allocated_qty: 0,
    shipped_qty: 0,
    unit_price: "9.99",
    currency: "GBP",
    external_line_id: null,
    needs_mapping: false,
    cost_per_unit_snapshot: "3.00",
    ...over,
  };
}

function order(over: Record<string, unknown> = {}) {
  return {
    id: 900,
    platform: "etsy",
    external_order_id: "E-900",
    status: "pending",
    buyer_name: "A. Buyer",
    buyer_note: null,
    order_placed_at: "2026-08-20T09:14:00Z",
    shipped_at: null,
    cancelled_at: null,
    notes: null,
    created_at: "2026-08-20T09:14:00Z",
    updated_at: "2026-08-20T09:14:00Z",
    grand_total: "19.98",
    subtotal: "19.98",
    shipping_charged: "3.60",
    shipping_profile_id: null,
    shipping_profile_name: null,
    shipping_cost_snapshot: null,
    tax_charged: null,
    vat_charged: null,
    discount_amount: null,
    refunded_amount: null,
    currency: "GBP",
    payment_fees: "1.44",
    payment_net: null,
    payment_status: "paid",
    financials_synced_at: null,
    materials_cogs: null,
    kitting_cogs: null,
    net_profit: "12.00",
    cogs_pending: false,
    postage_cost_missing: false,
    sync_issue: null,
    pending_marketplace_cancellation: false,
    lines: [line()],
    ...over,
  };
}

function routes(items: unknown[]) {
  return [
    { method: "GET" as const, path: "/system/status", respond: () => ({ status: "ok" }) },
    {
      method: "POST" as const,
      path: /^\/orders\/\d+\/allocate$/,
      respond: () => order({ status: "allocated" }),
    },
    { method: "GET" as const, path: /^\/orders\?/, respond: () => ({ items, total: items.length }) },
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ];
}

async function renderList() {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ["/orders"] }),
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", { name: "Orders" }, { timeout: 5000 });
  return router;
}

beforeEach(() => setRoutes(routes([order()])));

it("filters by status when a tab is clicked", async () => {
  const user = userEvent.setup();
  await renderList();

  await user.click(await screen.findByRole("button", { name: /Shipped/ }));

  await waitFor(() =>
    expect(
      calls.some((c) => c.method === "GET" && c.path.includes("status_filter=shipped")),
    ).toBe(true),
  );
});

it("shows a derived Fulfilment state with an Allocate action for a pending order", async () => {
  const user = userEvent.setup();
  await renderList();

  const row = (await screen.findByText("Hex Planter")).closest("tr")!;
  expect(within(row).getByText("Not allocated")).toBeInTheDocument();

  await user.click(within(row).getByRole("button", { name: "Allocate" }));

  await waitFor(() =>
    expect(
      calls.some((c) => c.method === "POST" && c.path === "/orders/900/allocate"),
    ).toBe(true),
  );
});

it("opens the slide-over on a row click", async () => {
  const user = userEvent.setup();
  const router = await renderList();

  await user.click(await screen.findByText("Hex Planter"));

  await waitFor(() =>
    expect(router.state.location.pathname).toBe("/orders/900"),
  );
});
