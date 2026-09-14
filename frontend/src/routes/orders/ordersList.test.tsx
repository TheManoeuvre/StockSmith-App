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
    variation_text: null,
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
    ship_by_date: "2026-08-22T00:00:00Z",
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
    tracking_number: null,
    carrier: null,
    lines: [line()],
    ...over,
  };
}

function routes(items: (typeof order extends (...a: never[]) => infer R ? R : never)[]) {
  // Mirrors the real backend's ordering (see list_orders) so tests that assert on row order
  // don't need to duplicate it: soonest ship-by first for "awaiting", newest-placed first
  // for the terminal tabs.
  const byTab = (tab: "awaiting" | "shipped" | "cancelled") =>
    items
      .filter((o) =>
        tab === "awaiting" ? o.status !== "shipped" && o.status !== "cancelled" : o.status === tab,
      )
      .sort((a, b) =>
        tab === "awaiting"
          ? new Date(a.ship_by_date ?? "9999").getTime() - new Date(b.ship_by_date ?? "9999").getTime()
          : new Date(b.order_placed_at).getTime() - new Date(a.order_placed_at).getTime(),
      );
  return [
    { method: "GET" as const, path: "/system/status", respond: () => ({ status: "ok" }) },
    {
      method: "POST" as const,
      path: /^\/orders\/\d+\/allocate$/,
      respond: () => order({ status: "allocated" }),
    },
    {
      method: "GET" as const,
      path: /^\/orders\?.*status_filter=awaiting/,
      respond: () => ({ items: byTab("awaiting"), total: byTab("awaiting").length }),
    },
    {
      method: "GET" as const,
      path: /^\/orders\?.*status_filter=shipped/,
      respond: () => ({ items: byTab("shipped"), total: byTab("shipped").length }),
    },
    {
      method: "GET" as const,
      path: /^\/orders\?.*status_filter=cancelled/,
      respond: () => ({ items: byTab("cancelled"), total: byTab("cancelled").length }),
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

it("filters the table by status tab, defaulting to Awaiting Shipment", async () => {
  const user = userEvent.setup();
  setRoutes(
    routes([
      order({
        id: 900,
        external_order_id: "E-900",
        status: "shipped",
        order_placed_at: "2026-08-25T09:00:00Z",
        lines: [line({ product_name: "Shipped Planter" })],
      }),
      order({
        id: 901,
        external_order_id: "E-901",
        status: "pending",
        order_placed_at: "2026-08-20T09:00:00Z",
        lines: [line({ order_id: 901, product_name: "Waiting Planter" })],
      }),
    ]),
  );
  await renderList();

  expect(await screen.findByText("Waiting Planter")).toBeInTheDocument();
  expect(screen.queryByText("Shipped Planter")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /Shipped/ }));

  expect(await screen.findByText("Shipped Planter")).toBeInTheDocument();
  expect(screen.queryByText("Waiting Planter")).not.toBeInTheDocument();
});

it("sorts the awaiting group by soonest ship-by date, not placed date", async () => {
  setRoutes(
    routes([
      order({
        id: 900,
        external_order_id: "E-900",
        status: "pending",
        order_placed_at: "2026-08-20T09:00:00Z",
        ship_by_date: "2026-09-01T00:00:00Z",
        lines: [line({ product_name: "Placed First, Due Later" })],
      }),
      order({
        id: 901,
        external_order_id: "E-901",
        status: "pending",
        order_placed_at: "2026-08-25T09:00:00Z",
        ship_by_date: "2026-08-28T00:00:00Z",
        lines: [line({ order_id: 901, product_name: "Placed Later, Due Sooner" })],
      }),
    ]),
  );
  await renderList();

  const dueSooner = await screen.findByText("Placed Later, Due Sooner");
  const dueLater = screen.getByText("Placed First, Due Later");

  // The row placed later but due sooner comes first in the DOM.
  expect(
    dueSooner.compareDocumentPosition(dueLater) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
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
