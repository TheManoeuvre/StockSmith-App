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

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { routeTree } = await import("../../routeTree.gen");

function order(over: Record<string, unknown> = {}) {
  return {
    id: 42,
    platform: null,
    external_order_id: null,
    status: "pending",
    buyer_name: "M. Keyes",
    buyer_note: null,
    order_placed_at: "2026-08-20T09:14:00Z",
    shipped_at: null,
    cancelled_at: null,
    notes: null,
    created_at: "2026-08-20T09:14:00Z",
    updated_at: "2026-08-20T09:14:00Z",
    grand_total: "9.99",
    subtotal: "9.99",
    shipping_charged: null,
    shipping_profile_id: null,
    shipping_profile_name: null,
    shipping_cost_snapshot: null,
    tax_charged: null,
    vat_charged: null,
    discount_amount: null,
    refunded_amount: null,
    currency: "GBP",
    payment_fees: null,
    payment_net: null,
    payment_status: "paid",
    financials_synced_at: null,
    materials_cogs: null,
    kitting_cogs: null,
    net_profit: null,
    cogs_pending: false,
    postage_cost_missing: false,
    sync_issue: null,
    pending_marketplace_cancellation: false,
    lines: [],
    ...over,
  };
}

const PROFILES = [
  {
    id: 4,
    name: "Small Parcel 48",
    is_archived: false,
    usage_count: 0,
    price: "3.60",
    cost_etsy: "3.65",
    cost_ebay: "3.65",
    cost_manual: "3.65",
    created_at: "",
    updated_at: "",
  },
];

function routes(o: Record<string, unknown>) {
  return [
    { method: "GET" as const, path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET" as const, path: "/orders/42", respond: () => order(o) },
    {
      method: "PATCH" as const,
      path: "/orders/42",
      respond: (body: unknown) => order({ ...o, ...(body as object) }),
    },
    { method: "GET" as const, path: "/shipping-profiles", respond: () => PROFILES },
    {
      method: "GET" as const,
      path: "/orders/42/kitting-overrides",
      respond: () => ({
        overrides: [],
        lines: [],
        effective_cost_total: "0.00",
        consumed_cost_total: "0.00",
      }),
    },
    { method: "GET" as const, path: /^\/orders\?/, respond: () => ({ items: [], total: 0 }) },
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ];
}

async function renderOrder(over: Record<string, unknown> = {}) {
  setRoutes(routes(over));
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ["/orders/42"] }),
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  await screen.findByText("M. Keyes", undefined, { timeout: 5000 });
  return router;
}

beforeEach(() => setRoutes([]));

it("renders the four tabs in the body under the stat tiles", async () => {
  await renderOrder();
  for (const name of ["Fulfilment", "Financials", "Shipping", "Timeline"]) {
    expect(await screen.findByRole("button", { name })).toBeInTheDocument();
  }
  expect(screen.getByText("Order value")).toBeInTheDocument();
});

it("commits a shipping-profile edit through the one footer Save", async () => {
  const user = userEvent.setup();
  await renderOrder();

  await user.click(await screen.findByRole("button", { name: "Shipping" }));
  const select = await screen.findByRole("combobox");
  await user.selectOptions(select, "4");

  const save = screen.getByRole("button", { name: "Save" });
  await waitFor(() => expect(save).toBeEnabled());
  await user.click(save);

  await waitFor(() =>
    expect(
      calls.some(
        (c) =>
          c.method === "PATCH" &&
          c.path === "/orders/42" &&
          (c.body as { shipping_profile_id?: number }).shipping_profile_id === 4,
      ),
    ).toBe(true),
  );
});

it("the footer Revert drops a shipping edit with no PATCH", async () => {
  const user = userEvent.setup();
  await renderOrder();

  await user.click(await screen.findByRole("button", { name: "Shipping" }));
  await user.selectOptions(await screen.findByRole("combobox"), "4");

  await user.click(screen.getByRole("button", { name: "Revert" }));

  await waitFor(() => expect(screen.getByText("No changes")).toBeInTheDocument());
  expect(calls.some((c) => c.method === "PATCH")).toBe(false);
});

it("synthesises a timeline from the order's timestamps", async () => {
  const user = userEvent.setup();
  await renderOrder({ status: "shipped", shipped_at: "2026-08-21T10:00:00Z" });

  await user.click(await screen.findByRole("button", { name: "Timeline" }));

  expect(await screen.findByText("Order placed")).toBeInTheDocument();
  expect(screen.getByText(/Marked shipped/)).toBeInTheDocument();
});
