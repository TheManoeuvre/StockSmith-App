import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { routeTree } = await import("../../routeTree.gen");

function line() {
  return {
    id: 1,
    order_id: 42,
    product_id: 7,
    variant_id: null,
    product_name: "Coaster",
    variant_name: null,
    sku: "SKU-A",
    ordered_qty: 1,
    allocated_qty: 1,
    shipped_qty: 0,
    unit_price: "5.00",
    currency: "GBP",
    external_line_id: null,
    needs_mapping: false,
    cost_per_unit_snapshot: null,
    variation_text: null,
    substituted_from: null,
    substituted_to: [],
  };
}

function order(over: Record<string, unknown> = {}) {
  return {
    id: 42,
    platform: null,
    external_order_id: null,
    status: "allocated",
    buyer_name: "M. Keyes",
    buyer_note: null,
    order_placed_at: "2026-08-20T09:14:00Z",
    shipped_at: null,
    ship_by_date: null,
    cancelled_at: null,
    notes: null,
    created_at: "2026-08-20T09:14:00Z",
    updated_at: "2026-08-20T09:14:00Z",
    grand_total: "9.99",
    subtotal: "9.99",
    currency: "GBP",
    materials_cogs: null,
    kitting_cogs: null,
    net_profit: null,
    cogs_pending: false,
    postage_cost_missing: false,
    replacement_parcels_need_review: false,
    postage_charges: [],
    replacement_parcels: [],
    sync_issue: null,
    pending_marketplace_cancellation: false,
    needs_box_check: false,
    box_check_confirmed_at: null,
    tracking_number: null,
    carrier: null,
    lines: [line()],
    ...over,
  };
}

const BOXES = [
  { material_id: 1, material_name: "Small Box", is_candidate: true, qty: "1", unit_cost: "0.40", free_stock: "9" },
  { material_id: 2, material_name: "Large Box", is_candidate: true, qty: "1", unit_cost: "0.90", free_stock: "9" },
];

function boxCheck(over: Record<string, unknown> = {}) {
  return { mode: "pre_ship", applies: true, needs_check: true, confirmed_at: null, boxes: BOXES, ...over };
}

function renderOrder(orderOver: Record<string, unknown>, check: Record<string, unknown>, extra: unknown[] = []) {
  setRoutes([
    { method: "GET", path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET", path: "/orders/42", respond: () => order(orderOver) },
    { method: "GET", path: "/orders/42/box-check", respond: () => boxCheck(check) },
    {
      method: "GET",
      path: "/orders/42/kitting-overrides",
      respond: () => ({ overrides: [], lines: [], effective_cost_total: "0.00", consumed_cost_total: "0.00" }),
    },
    {
      method: "POST",
      path: "/orders/42/box-check/preview",
      respond: () => ({
        changes: [
          { material_id: 1, material_name: "Small Box", current_qty: "1", new_qty: "0", stock_effect: "1" },
        ],
        kitting_cost_before: "1.30",
        kitting_cost_after: "0.90",
        net_profit_delta: "0.40",
        warnings: [],
      }),
    },
    ...(extra as never[]),
    { method: "GET", path: /^\/orders\?/, respond: () => ({ items: [], total: 0 }) },
    { method: "GET", path: /.*/, respond: () => [] },
  ]);
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: ["/orders/42"] }) });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
}

beforeEach(() => setRoutes([]));

it("blocks shipping and asks which box was used when an order needs a check", async () => {
  renderOrder({ needs_box_check: true }, {});

  expect(await screen.findByText(/can't be shipped until you confirm which was used/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ship" })).toBeDisabled();
  // Nothing is pre-ticked, so Confirm stays disabled until a person chooses.
  expect(screen.getByRole("checkbox", { name: "Small Box used" })).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Confirm boxes" })).toBeDisabled();
});

it("confirms the ticked box with its quantity", async () => {
  const user = userEvent.setup();
  renderOrder({ needs_box_check: true }, {}, [
    { method: "PUT", path: "/orders/42/box-check", respond: () => order() },
  ]);

  await user.click(await screen.findByRole("checkbox", { name: "Large Box used" }));
  const confirm = screen.getByRole("button", { name: "Confirm boxes" });
  await waitFor(() => expect(confirm).toBeEnabled());
  await user.click(confirm);

  await waitFor(() => {
    const put = calls.find((c) => c.method === "PUT" && c.path === "/orders/42/box-check");
    expect(put?.body).toEqual({ boxes: [{ material_id: 2, qty: "1" }], return_to_stock: true });
  });
});

it("lets more than one box be approved", async () => {
  const user = userEvent.setup();
  renderOrder({ needs_box_check: true }, {}, [
    { method: "PUT", path: "/orders/42/box-check", respond: () => order() },
  ]);

  await user.click(await screen.findByRole("checkbox", { name: "Small Box used" }));
  await user.click(screen.getByRole("checkbox", { name: "Large Box used" }));
  await user.click(screen.getByRole("button", { name: "Confirm boxes" }));

  await waitFor(() => {
    const put = calls.find((c) => c.method === "PUT");
    expect((put?.body as { boxes: unknown[] }).boxes).toHaveLength(2);
  });
});

it("shows nothing when the order doesn't use several boxes", async () => {
  renderOrder({}, { applies: false, needs_check: false, boxes: [] });

  await screen.findByText("M. Keyes");
  expect(screen.queryByTestId("box-check")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ship" })).toBeEnabled();
});

it("previews the stock and profit effect of correcting a shipped order", async () => {
  const user = userEvent.setup();
  renderOrder(
    { status: "shipped", shipped_at: "2026-08-21T10:00:00Z", needs_box_check: true },
    { mode: "shipped" },
    [{ method: "POST", path: "/orders/42/box-check/reviewed", respond: () => order({ status: "shipped" }) }],
  );

  await user.click(await screen.findByRole("checkbox", { name: "Small Box used" }));
  // Unticking the small box (it starts ticked: both were consumed) should preview a restock.
  expect(await screen.findByText(/1 returned to stock/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Correct boxes" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Mark reviewed, no change" }));
  await waitFor(() =>
    expect(calls.some((c) => c.method === "POST" && c.path === "/orders/42/box-check/reviewed")).toBe(true),
  );
});
