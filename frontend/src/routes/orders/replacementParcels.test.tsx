import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { routeTree } = await import("../../routeTree.gen");

const LABEL_1 = {
  id: 11,
  platform: "ebay",
  source: "ebay_shipping_label",
  external_id: "05-1",
  amount: "3.10",
  currency: "GBP",
  posted_at: "2026-08-21T10:00:00Z",
  description: "Shipping label purchased",
  sequence: 1,
  replacement_parcel_id: null,
};
const LABEL_2 = { ...LABEL_1, id: 12, external_id: "05-2", amount: "3.40", posted_at: "2026-08-26T10:00:00Z", sequence: 2 };

const SYNC_PARCEL = {
  id: 7,
  order_id: 42,
  reason: "unspecified",
  source: "sync",
  needs_review: true,
  postage_cost: null,
  effective_postage: "3.40",
  postage_charge: { ...LABEL_2, replacement_parcel_id: 7 },
  tracking_number: null,
  carrier: null,
  notes: "Auto-created from ebay shipping label 05-2",
  sent_at: "2026-08-26T10:00:00Z",
  created_at: "2026-08-26T10:05:00Z",
  items: [],
  items_cost: null,
};

const MANUAL_PARCEL = {
  id: 8,
  order_id: 42,
  reason: "missing_from_order",
  source: "manual",
  needs_review: false,
  postage_cost: "2.50",
  effective_postage: "2.50",
  postage_charge: null,
  tracking_number: "RM123",
  carrier: "Royal Mail",
  notes: null,
  sent_at: "2026-08-27T10:00:00Z",
  created_at: "2026-08-27T10:00:00Z",
  items: [
    {
      id: 1,
      product_id: 5,
      variant_id: null,
      material_id: null,
      product_name: "Widget",
      variant_name: null,
      material_name: null,
      material_unit: null,
      qty: "1.0000",
      unit_cost_snapshot: "0.200000",
      line_cost: "0.20",
    },
    {
      id: 2,
      product_id: null,
      variant_id: null,
      material_id: 9,
      product_name: null,
      variant_name: null,
      material_name: "Box",
      material_unit: "each",
      qty: "1.0000",
      unit_cost_snapshot: "0.500000",
      line_cost: "0.50",
    },
  ],
  items_cost: "0.70",
};

function order(over: Record<string, unknown> = {}) {
  return {
    id: 42,
    platform: "ebay",
    manual_channel: null,
    external_order_id: "26-14962",
    status: "shipped",
    buyer_name: "M. Keyes",
    buyer_note: null,
    order_placed_at: "2026-08-20T09:14:00Z",
    shipped_at: "2026-08-21T10:00:00Z",
    ship_by_date: null,
    cancelled_at: null,
    notes: null,
    created_at: "2026-08-20T09:14:00Z",
    updated_at: "2026-08-20T09:14:00Z",
    grand_total: "20.00",
    subtotal: "20.00",
    shipping_charged: "0.00",
    shipping_profile_id: 4,
    shipping_profile_name: "Small Parcel 48",
    shipping_cost_snapshot: "3.65",
    tax_charged: null,
    vat_charged: null,
    discount_amount: null,
    refunded_amount: null,
    currency: "GBP",
    payment_fees: "3.45",
    payment_net: null,
    payment_status: "paid",
    financials_synced_at: null,
    materials_cogs: "2.00",
    kitting_cogs: null,
    net_profit: "11.45",
    cogs_pending: false,
    postage_cost_missing: false,
    postage_cost_actual: "3.10",
    postage_cost_effective: "3.10",
    replacement_postage: null,
    replacement_cogs: null,
    replacement_parcels_need_review: false,
    postage_charges: [LABEL_1],
    replacement_parcels: [],
    sync_issue: null,
    pending_marketplace_cancellation: false,
    tracking_number: null,
    carrier: null,
    lines: [
      {
        id: 1,
        order_id: 42,
        product_id: 5,
        variant_id: null,
        product_name: "Widget",
        variant_name: null,
        sku: "WID",
        ordered_qty: 1,
        allocated_qty: 1,
        shipped_qty: 1,
        unit_price: "20.00",
        currency: "GBP",
        external_line_id: "L1",
        needs_mapping: false,
        cost_per_unit_snapshot: "2.00",
        variation_text: null,
        substituted_from: null,
        substituted_to: [],
      },
    ],
    ...over,
  };
}

const PRODUCTS = [{ id: 5, name: "Widget", sku: "WID", current_stock: 4, allocated_qty: 0 }];
const MATERIALS = [
  { id: 9, name: "Box", category: "packaging", category_id: 1, unit: "each", current_qty: "9", avg_unit_cost: "0.50" },
];

function routes(o: Record<string, unknown>) {
  let current = order(o);
  return [
    { method: "GET" as const, path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET" as const, path: "/orders/42", respond: () => current },
    {
      method: "POST" as const,
      path: "/orders/42/replacement-parcels",
      respond: () => {
        current = order({ ...o, replacement_parcels: [MANUAL_PARCEL], replacement_postage: "2.50" });
        return current;
      },
    },
    {
      method: "DELETE" as const,
      path: "/orders/replacement-parcels/8",
      respond: () => {
        current = order(o);
        return current;
      },
    },
    { method: "GET" as const, path: /^\/products\?/, respond: () => ({ items: PRODUCTS, total: PRODUCTS.length }) },
    { method: "GET" as const, path: "/products/5/variants", respond: () => [] },
    { method: "GET" as const, path: "/materials", respond: () => MATERIALS },
    { method: "GET" as const, path: "/material-categories", respond: () => [] },
    {
      method: "GET" as const,
      path: "/orders/42/kitting-overrides",
      respond: () => ({
        overrides: [],
        lines: [
          {
            material_id: 9,
            material_name: "Box",
            auto_qty: "1",
            effective_qty: "1",
            reserved_qty: "0",
            consumed_qty: "1",
            unit_cost: "0.50",
            unit_cost_is_frozen: true,
            effective_cost: "0.50",
            consumed_cost: "0.50",
          },
        ],
        effective_cost_total: "0.50",
        consumed_cost_total: "0.50",
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

it("offers only the record button until there is a parcel to show", async () => {
  await renderOrder();
  expect(await screen.findByRole("button", { name: "Record replacement parcel" })).toBeEnabled();
  expect(screen.queryByRole("region", { name: "Replacement parcels" })).not.toBeInTheDocument();
});

it("renders recorded parcels apart from the order lines, with their items and costs", async () => {
  await renderOrder({ replacement_parcels: [MANUAL_PARCEL], replacement_postage: "2.50", replacement_cogs: "0.70" });
  const section = await screen.findByRole("region", { name: "Replacement parcels" });
  expect(within(section).getByText("Missing from order")).toBeInTheDocument();
  expect(within(section).getByText("Widget")).toBeInTheDocument();
  expect(within(section).getByText("Box")).toBeInTheDocument();
  expect(within(section).getByText("RM123")).toBeInTheDocument();
  expect(within(section).queryByText("Needs completing")).not.toBeInTheDocument();
});

it("flags a sync-created parcel for completion, with a banner that jumps to it", async () => {
  await renderOrder({
    replacement_parcels: [SYNC_PARCEL],
    replacement_parcels_need_review: true,
    replacement_postage: "3.40",
    postage_charges: [LABEL_1, { ...LABEL_2, replacement_parcel_id: 7 }],
  });
  expect(await screen.findByText(/Replacement parcel detected/)).toBeInTheDocument();
  const section = screen.getByRole("region", { name: "Replacement parcels" });
  expect(within(section).getByText("Needs completing")).toBeInTheDocument();
  expect(within(section).getByText("Reason not set")).toBeInTheDocument();
  expect(within(section).getByText(/eBay label #2/)).toBeInTheDocument();
  expect(within(section).getByRole("button", { name: "Complete" })).toBeInTheDocument();
});

it("records a parcel pre-filled from the original shipment and posts it", async () => {
  const user = userEvent.setup();
  await renderOrder();
  await user.click(await screen.findByRole("button", { name: "Record replacement parcel" }));

  const dialog = await screen.findByRole("dialog", { name: "Record replacement parcel" });
  // Pre-filled: the order's own product and its packaging.
  const productSelect = await within(dialog).findByRole("combobox", { name: "Product" });
  await waitFor(() => expect(productSelect).toHaveValue("5"));
  await waitFor(() => expect(within(dialog).getByLabelText("Packaging quantity")).toHaveValue(1));
  await user.selectOptions(within(dialog).getByDisplayValue("Missing from order"), "faulty_item");
  await user.type(within(dialog).getByLabelText("Postage cost"), "2.50");
  await user.click(within(dialog).getByRole("button", { name: "Record parcel" }));

  await waitFor(() =>
    expect(calls.some((c) => c.method === "POST" && c.path === "/orders/42/replacement-parcels")).toBe(true),
  );
  const body = calls.find((c) => c.method === "POST")!.body as {
    reason: string;
    postage_cost: string | null;
    items: { product_id?: number; material_id?: number; qty: string }[];
  };
  expect(body.reason).toBe("faulty_item");
  expect(body.postage_cost).toBe("2.50");
  expect(body.items).toEqual([
    { product_id: 5, variant_id: null, qty: "1" },
    { material_id: 9, qty: "1" },
  ]);
  expect(await screen.findByRole("region", { name: "Replacement parcels" })).toBeInTheDocument();
});

it("shows replacement figures on the Financials tab and actual vs estimate on Shipping", async () => {
  const user = userEvent.setup();
  await renderOrder({
    replacement_parcels: [MANUAL_PARCEL],
    replacement_postage: "2.50",
    replacement_cogs: "0.70",
    postage_charges: [LABEL_1, LABEL_2],
  });

  await user.click(await screen.findByRole("button", { name: "Financials" }));
  expect(await screen.findByText("Replacement postage")).toBeInTheDocument();
  expect(screen.getByText("Replacement COGS")).toBeInTheDocument();
  expect(screen.getByText(/Actual label · est\. £3\.65/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Shipping" }));
  expect(await screen.findByText("Postage estimate")).toBeInTheDocument();
  expect(screen.getByText("Postage actual")).toBeInTheDocument();
  expect(screen.getByText("Additional labels")).toBeInTheDocument();
  expect(screen.getByText("Unlinked")).toBeInTheDocument();
});

it("deleting a parcel asks first and says what comes back into stock", async () => {
  const user = userEvent.setup();
  await renderOrder({ replacement_parcels: [MANUAL_PARCEL], replacement_postage: "2.50" });
  const section = await screen.findByRole("region", { name: "Replacement parcels" });
  await user.click(within(section).getByRole("button", { name: "Delete" }));

  const dialog = await screen.findByRole("dialog", { name: "Delete this replacement parcel?" });
  expect(within(dialog).getByText(/1 product unit and 1 packaging item will be returned to stock/)).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Delete parcel" }));

  await waitFor(() =>
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/orders/replacement-parcels/8")).toBe(true),
  );
});
