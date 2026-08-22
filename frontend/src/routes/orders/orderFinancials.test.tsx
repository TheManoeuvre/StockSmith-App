import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
}));

const { setRoutes } = await import("../../test/fakeBackend");
const { routeTree } = await import("../../routeTree.gen");

/** Order 4149598334: £6.99 of goods less a £1.40 coupon, £3.60 postage, netting £3.22. */
function order(overrides: Record<string, unknown> = {}) {
  return {
    id: 153,
    platform: "etsy",
    external_order_id: "4149598334",
    status: "shipped",
    order_placed_at: "2026-08-19T17:14:10Z",
    currency: "GBP",
    subtotal: "5.59",
    shipping_charged: "3.60",
    discount_amount: "1.40",
    refunded_amount: null,
    payment_fees: "1.44",
    shipping_cost_snapshot: "3.65",
    shipping_profile_id: 4,
    shipping_profile_name: "Small Parcel 48",
    materials_cogs: "0.65",
    kitting_cogs: "0.23",
    net_profit: "3.22",
    cogs_pending: false,
    postage_cost_missing: false,
    lines: [],
    ...overrides,
  };
}

function routes(o: Record<string, unknown>) {
  return [
    { method: "GET" as const, path: "/orders/153", respond: () => o },
    { method: "GET" as const, path: "/system/status", respond: () => ({ status: "ok" }) },
  ];
}

async function renderOrder(overrides: Record<string, unknown> = {}) {
  setRoutes(routes(order(overrides)));
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: ["/orders/153"] }) });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  await screen.findByText("Order value & costs");
}

beforeEach(() => {
  setRoutes([]);
});

it("shows what the discount came off, rather than listing it as another deduction", async () => {
  await renderOrder();

  // The £6.99 is not stored anywhere — subtotal is already net of the coupon, so the
  // original is subtotal + discount. Showing it under the figure it explains is the whole
  // point: as a column of its own it read as a second deduction from the same money.
  expect(screen.getByText("£6.99 − £1.40 discount")).toBeInTheDocument();
  expect(screen.getByText("£5.59")).toBeInTheDocument();
  expect(screen.queryByText("Discount")).not.toBeInTheDocument();
});

it("says nothing about a discount when there wasn't one", async () => {
  await renderOrder({ subtotal: "6.99", discount_amount: null, net_profit: "1.82" });

  expect(screen.queryByText(/discount/i)).not.toBeInTheDocument();
});

it("treats a zero discount as no discount", async () => {
  // eBay reports 0.00 rather than omitting the field on some orders, and "£6.99 − £0.00
  // discount" under every one of them is noise.
  await renderOrder({ subtotal: "6.99", discount_amount: "0.00", net_profit: "1.82" });

  expect(screen.queryByText(/discount/i)).not.toBeInTheDocument();
});

it("puts the shipping profile under the postage cost instead of in its heading", async () => {
  await renderOrder();

  // The name is an identifier, not a figure — in the heading it made one column twice the
  // width of every other and put a proper noun in a row of money.
  expect(screen.getByText("Postage cost")).toBeInTheDocument();
  expect(screen.queryByText("Postage cost (Small Parcel 48)")).not.toBeInTheDocument();
  expect(screen.getByText("Small Parcel 48")).toBeInTheDocument();
});

it("omits the profile line when no profile is assigned", async () => {
  await renderOrder({ shipping_profile_name: null, shipping_profile_id: null });

  expect(screen.getByText("Postage cost")).toBeInTheDocument();
  expect(screen.queryByText("Small Parcel 48")).not.toBeInTheDocument();
});

it("keeps a refund as its own figure", async () => {
  // Unlike the discount, a refund really is deducted in _compute_net_profit, so it belongs
  // with the things the top row sums rather than as a note under one of them.
  await renderOrder({ refunded_amount: "2.00", net_profit: "1.22" });

  expect(screen.getByText("Refunded")).toBeInTheDocument();
  expect(screen.getByText("-£2.00")).toBeInTheDocument();
});

it("leaves a row that adds up to the net profit beneath it", async () => {
  await renderOrder();

  // 5.59 + 3.60 - 1.44 - 3.65 - 0.65 - 0.23 = 3.22. Every figure on the row is now either
  // added or subtracted exactly once, which was the complaint: the discount sat among them
  // looking like a deduction while net profit correctly ignored it.
  for (const shown of ["£5.59", "£3.60", "-£1.44", "-£3.65", "-£0.65", "-£0.23", "£3.22"]) {
    expect(screen.getByText(shown)).toBeInTheDocument();
  }
});

it("says the postage cost was never recorded, rather than showing a bare dash", async () => {
  // The order shipped without a shipping profile, so _compute_net_profit deducted nothing
  // for postage. A dash reads as "nothing to show"; this figure is wrong, and says so.
  await renderOrder({
    shipping_cost_snapshot: null,
    shipping_profile_id: null,
    shipping_profile_name: null,
    postage_cost_missing: true,
    net_profit: "6.87",
  });

  expect(screen.getByText("Not recorded")).toBeInTheDocument();
  expect(screen.getByText(/shipped without a shipping profile/)).toBeInTheDocument();
});

it("keeps quiet about postage on an order that hasn't shipped yet", async () => {
  // shipping_cost_snapshot is legitimately null until ship_order freezes it, so an
  // unshipped order must not be flagged — the backend's status gate is what decides this.
  await renderOrder({ status: "allocated", shipping_cost_snapshot: null, postage_cost_missing: false });

  expect(screen.queryByText("Not recorded")).not.toBeInTheDocument();
  expect(screen.queryByText(/shipped without a shipping profile/)).not.toBeInTheDocument();
});
