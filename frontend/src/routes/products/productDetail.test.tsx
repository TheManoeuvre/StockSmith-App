import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRouter,
} from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () =>
  (await import("../../test/fakeBackend")).clientMock(),
);

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { routeTree } = await import("../../routeTree.gen");

const PRODUCT = {
  id: 1,
  name: "Doorbell Mount",
  sku: "DM-1",
  description: null,
  barcode: null,
  is_bundle: false,
  is_active: true,
  current_stock: 5,
  allocated_qty: 0,
  cost_per_unit: "1.00",
  kitting_cost_per_unit: "0.30",
  pricing_mode: "product",
  push_buildable_capacity: true,
  made_to_order: false,
  platform_ceiling_qty: null,
  variant_attribute1_name: null,
  variant_attribute2_name: null,
  variant_attribute3_name: null,
  sale_price: null,
  shipping_profile_id: null,
  platform_fee_percent: null,
  effective_platform_fee_percent: null,
  pricing_variable_attribute: null,
  max_sellable: 5,
  theoretical_max_sellable: 5,
};

const MATERIALS = [
  {
    id: 1,
    name: "Filament",
    unit: "g",
    category: "filament",
    category_id: 1,
    current_qty: "100",
    allocated_qty: "0",
    avg_unit_cost: "0.50",
  },
  {
    id: 2,
    name: "Box",
    unit: "each",
    category: "packaging",
    category_id: 2,
    current_qty: "50",
    allocated_qty: "0",
    avg_unit_cost: "0.30",
  },
];

// The categories the pages read behaviour off. Without these the catch-all below answers with
// an empty list, and every category-gated field silently disappears rather than failing — so
// these tests would keep passing while testing nothing.
const CATEGORIES = [
  {
    id: 1,
    name: "filament",
    sort_order: 10,
    default_unit: "g",
    consumed_on_failed_build: true,
    auto_kitting_per_order: false,
    tracks_colour: true,
    tracks_material_type: true,
    cost_per_kg_display: true,
    usage_count: 1,
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: 2,
    name: "packaging",
    sort_order: 20,
    default_unit: "each",
    consumed_on_failed_build: false,
    auto_kitting_per_order: true,
    tracks_colour: false,
    tracks_material_type: false,
    cost_per_kg_display: false,
    usage_count: 1,
    created_at: "2026-01-01T00:00:00Z",
  },
];

function baseRoutes(product: Record<string, unknown> = PRODUCT) {
  return [
    { method: "GET" as const, path: "/products/1", respond: () => product },
    { method: "GET" as const, path: "/products/1/variants", respond: () => [] },
    {
      method: "GET" as const,
      path: "/products/1/bom",
      respond: () => [
        { id: 1, product_id: 1, material_id: 1, qty_required: "2" },
      ],
    },
    {
      method: "GET" as const,
      path: "/products/1/kitting-bom",
      respond: () => [
        { id: 2, product_id: 1, material_id: 2, qty_required: "1" },
      ],
    },
    {
      method: "GET" as const,
      path: "/products/1/bundle-items",
      respond: () => [],
    },
    { method: "GET" as const, path: "/materials", respond: () => MATERIALS },
    {
      method: "GET" as const,
      path: "/material-categories",
      respond: () => CATEGORIES,
    },
    {
      method: "PUT" as const,
      path: "/products/1/bom",
      respond: (body: unknown) => body,
    },
    {
      method: "PUT" as const,
      path: "/products/1/kitting-bom",
      respond: (body: unknown) => body,
    },
    // Anything else the page or root layout reaches for (assets, sync status, price history…)
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ];
}

const variant = (
  id: number,
  name: string,
  overrides: Record<string, unknown> = {},
) => ({
  id,
  product_id: 1,
  variant_name: name,
  sku_suffix: null,
  is_active: true,
  current_stock: 0,
  allocated_qty: 0,
  attribute1_value: name,
  attribute2_value: null,
  attribute3_value: null,
  sale_price: "10.00",
  shipping_profile_id: null,
  effective_shipping_profile_id: null,
  platform_fee_percent: null,
  effective_platform_fee_percent: null,
  max_buildable: 1,
  expected_max_buildable: 1,
  max_sellable: 1,
  max_sellable_reason: null,
  expected_max_sellable: 1,
  expected_max_sellable_reason: null,
  theoretical_max_sellable: 1,
  theoretical_max_sellable_reason: null,
  cost_per_unit: "1.00",
  kitting_cost_per_unit: "0.30",
  effective_bom: [
    {
      material_id: 1,
      qty_required: "2",
      replaces_material_id: null,
      line_max_buildable: 50,
    },
  ],
  effective_kitting_bom: [
    {
      material_id: 2,
      qty_required: "1",
      replaces_material_id: null,
      line_max_buildable: 50,
    },
  ],
  full_sku: `DM-1-${id}`,
  ...overrides,
});

/** Adds the endpoints the Variants and Pricing tabs reach for, on top of baseRoutes. */
function withVariants(
  variants: ReturnType<typeof variant>[],
  product: Record<string, unknown> = PRODUCT,
) {
  return [
    {
      method: "GET" as const,
      path: "/products/1/variants",
      respond: () => variants,
    },
    ...variants.map((v) => ({
      method: "GET" as const,
      path: `/variants/${v.id}`,
      respond: () => v,
    })),
    {
      method: "PATCH" as const,
      path: /^\/variants\/\d+$/,
      respond: (body: unknown) => body,
    },
    {
      method: "GET" as const,
      path: "/settings/margin-fee-config",
      respond: () => ({ fee_source: "manual" }),
    },
    { method: "GET" as const, path: "/shipping-profiles", respond: () => [] },
    {
      method: "PATCH" as const,
      path: "/products/1",
      respond: (body: unknown) => ({ ...product, ...(body as object) }),
    },
    ...baseRoutes(product),
  ];
}

async function renderProductPage(initialEntry = "/products/1") {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [initialEntry] }),
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      <RouterProvider router={router as any} />
    </QueryClientProvider>,
  );
  // Generous timeout: the first render in a file resolves the whole lazy route tree.
  // *AllBy*, not a bare query: the panel's own title (DetailPanel's h1) and the Details
  // tab's identity block (its own h1 — see $productId.tsx) both say "Doorbell Mount".
  await screen.findAllByRole(
    "heading",
    { name: "Doorbell Mount" },
    { timeout: 5000 },
  );
  return router;
}

const bomTab = () => screen.getByRole("button", { name: "BOM" });
const pricingTab = () => screen.getByRole("button", { name: "Pricing" });

/** BOM tables only — other tabs render tables of their own. */
const bomTables = () =>
  screen.getAllByRole("table").filter((t) => within(t).queryByText("Cover (builds)"));

/**
 * The build BOM's qty input. Async because the Save button renders before the BOM query
 * resolves, so waiting on the button alone isn't enough to know the rows are there.
 */
const buildQtyInput = async () => (await screen.findAllByDisplayValue("2"))[0];

describe("product detail page", () => {
  beforeEach(() => setRoutes(baseRoutes()));

  it("puts both BOM tables under one BOM tab", async () => {
    const user = userEvent.setup();
    await renderProductPage();

    // The separate "Kitting BOM" tab is gone; the heading inside the merged tab remains.
    expect(
      screen.queryByRole("button", { name: "Kitting BOM" }),
    ).not.toBeInTheDocument();

    await user.click(bomTab());

    expect(
      await screen.findByRole("heading", { name: "Build BOM" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Kitting BOM" }),
    ).toBeInTheDocument();
    expect(bomTables()).toHaveLength(2);
  });

  it("records the open tab in the URL", async () => {
    const user = userEvent.setup();
    const router = await renderProductPage();

    await user.click(bomTab());
    await waitFor(() =>
      expect(router.state.location.search).toEqual({ tab: "bom" }),
    );
  });

  it("arms the one slide-over Save when a BOM table is edited", async () => {
    const user = userEvent.setup();
    await renderProductPage();
    await user.click(bomTab());

    // The slide-over has a single footer Save now, not one per editor.
    expect(
      screen.queryByRole("button", { name: /save build bom/i }),
    ).not.toBeInTheDocument();
    const footerSave = screen.getByRole("button", { name: "Save" });
    expect(footerSave).toBeDisabled();

    const qty = await buildQtyInput();
    await user.clear(qty);
    await user.type(qty, "3");

    await waitFor(() => expect(footerSave).toBeEnabled());
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
  });

  it("warns before a tab switch would discard edits, and stays put on Keep editing", async () => {
    const user = userEvent.setup();
    const router = await renderProductPage();
    await user.click(bomTab());

    const qty = await buildQtyInput();
    await user.clear(qty);
    await user.type(qty, "9");

    await user.click(pricingTab());

    const dialog = await screen.findByRole("dialog", {
      name: "Unsaved changes",
    });
    // Names what is unsaved, rather than a bare "you have unsaved changes". Scoped to the
    // dialog because "Build BOM" is also the heading on the page behind it.
    expect(within(dialog).getByText("Build BOM")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /keep editing/i }));

    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /keep editing/i }),
      ).not.toBeInTheDocument(),
    );
    expect(router.state.location.search).toEqual({ tab: "bom" });
    expect(screen.getAllByDisplayValue("9")[0]).toBeInTheDocument(); // edit survived
  });

  it("lets the navigation through on Discard changes", async () => {
    const user = userEvent.setup();
    const router = await renderProductPage();
    await user.click(bomTab());

    const qty = await buildQtyInput();
    await user.clear(qty);
    await user.type(qty, "9");

    await user.click(pricingTab());
    await screen.findByRole("button", { name: /discard changes/i });
    await user.click(screen.getByRole("button", { name: /discard changes/i }));

    await waitFor(() =>
      expect(router.state.location.search).toEqual({ tab: "pricing" }),
    );
    expect(
      calls.some((c) => c.method === "PUT" && c.path === "/products/1/bom"),
    ).toBe(false);
  });

  it("does not warn when nothing has been edited", async () => {
    const user = userEvent.setup();
    const router = await renderProductPage();
    await user.click(bomTab());
    await screen.findByRole("heading", { name: "Build BOM" });

    await user.click(pricingTab());

    await waitFor(() =>
      expect(router.state.location.search).toEqual({ tab: "pricing" }),
    );
    expect(
      screen.queryByRole("button", { name: /keep editing/i }),
    ).not.toBeInTheDocument();
  });

  it("stops warning once the edit is saved", async () => {
    const user = userEvent.setup();
    const router = await renderProductPage();
    await user.click(bomTab());

    const qty = await buildQtyInput();
    await user.clear(qty);
    await user.type(qty, "3");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(
        calls.some((c) => c.method === "PUT" && c.path === "/products/1/bom"),
      ).toBe(true),
    );

    await user.click(pricingTab());

    await waitFor(() =>
      expect(router.state.location.search).toEqual({ tab: "pricing" }),
    );
    expect(
      screen.queryByRole("button", { name: /keep editing/i }),
    ).not.toBeInTheDocument();
  });

  it("the footer Revert discards a buffered edit without a PUT", async () => {
    const user = userEvent.setup();
    await renderProductPage();
    await user.click(bomTab());

    const qty = await buildQtyInput();
    await user.clear(qty);
    await user.type(qty, "7");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Revert" }));

    await waitFor(() =>
      expect(screen.getAllByDisplayValue("2")[0]).toBeInTheDocument(),
    );
    expect(screen.getByText("No changes")).toBeInTheDocument();
    expect(
      calls.some((c) => c.method === "PUT" && c.path === "/products/1/bom"),
    ).toBe(false);
  });

  it("shows the kitting table for a bundle too", async () => {
    // Bundles really do consume packaging — apply_default_kitting_bom runs for them and order
    // fulfilment resolves a kitting BOM for every line, so hiding this would make a bundle
    // quietly eat boxes with nowhere to see or edit them.
    const user = userEvent.setup();
    setRoutes(
      baseRoutes({ ...PRODUCT, is_bundle: true, kitting_cost_per_unit: null }),
    );
    await renderProductPage();

    await user.click(bomTab());

    expect(
      await screen.findByRole("heading", { name: "Bundle components" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Kitting BOM" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Build BOM" }),
    ).not.toBeInTheDocument();
  });

  it("shows each line's cost with a table total", async () => {
    const user = userEvent.setup();
    await renderProductPage();
    await user.click(bomTab());

    await buildQtyInput();
    const buildTable = bomTables()[0];
    // 2 x £0.50
    expect(within(buildTable).getAllByText("£1.00")).toHaveLength(2); // row + footer total
    expect(within(buildTable).getByText("Total")).toBeInTheDocument();
  });
});

const variantsTab = () => screen.getByRole("button", { name: "Variants" });

describe("variant paths", () => {
  const two = [variant(11, "Red"), variant(12, "Blue")];

  it("opens the tab on the variants, not the attributes form, once variants exist", async () => {
    const user = userEvent.setup();
    setRoutes(withVariants(two));
    await renderProductPage();
    await user.click(variantsTab());

    await screen.findByRole("button", { name: /red/i });
    expect(
      screen.queryByRole("button", { name: "Generate variants" }),
    ).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: /variant attributes/i }),
    );
    expect(
      screen.getByRole("button", { name: "Generate variants" }),
    ).toBeInTheDocument();
  });

  it("leaves the attributes form open for a product with no variants yet", async () => {
    const user = userEvent.setup();
    setRoutes(withVariants([]));
    await renderProductPage();
    await user.click(variantsTab());

    expect(
      await screen.findByRole("button", { name: "Generate variants" }),
    ).toBeInTheDocument();
  });

  it("warns before collapsing the attributes form with unsubmitted input", async () => {
    const user = userEvent.setup();
    setRoutes(withVariants([]));
    await renderProductPage();
    await user.click(variantsTab());

    // Nothing saves this input until "Generate variants" consumes it, so collapsing —
    // which unmounts the form — has to ask first.
    await user.type(
      await screen.findByPlaceholderText("Size, Colour…"),
      "Colour",
    );
    await user.click(
      screen.getByRole("button", { name: /variant attributes/i }),
    );

    const dialog = await screen.findByRole("dialog", {
      name: "Unsaved changes",
    });
    expect(within(dialog).getByText(/Variant attributes/)).toBeInTheDocument();
  });

  it("warns before collapsing a variant row with unsaved edits", async () => {
    const user = userEvent.setup();
    setRoutes(withVariants(two));
    await renderProductPage();
    await user.click(variantsTab());

    // Expand Red and rename it without saving.
    await user.click(await screen.findByRole("button", { name: /red/i }));
    const nameInput = await screen.findByDisplayValue("Red");
    await user.clear(nameInput);
    await user.type(nameInput, "Crimson");

    // Collapsing unmounts the row's editors, so it has to ask first.
    await user.click(screen.getByRole("button", { name: /crimson|red/i }));

    const dialog = await screen.findByRole("dialog", {
      name: "Unsaved changes",
    });
    expect(within(dialog).getByText(/Variant "Red"/)).toBeInTheDocument();

    await user.click(
      within(dialog).getByRole("button", { name: /keep editing/i }),
    );
    expect(await screen.findByDisplayValue("Crimson")).toBeInTheDocument();
  });

  it("does not warn about a dirty row when a DIFFERENT row is toggled", async () => {
    // Prefix isolation, end to end: variant-11/ must not be matched by variant-12/'s check.
    const user = userEvent.setup();
    setRoutes(withVariants(two));
    await renderProductPage();
    await user.click(variantsTab());

    await user.click(await screen.findByRole("button", { name: /red/i }));
    const nameInput = await screen.findByDisplayValue("Red");
    await user.clear(nameInput);
    await user.type(nameInput, "Crimson");

    // Expanding Blue collapses Red (single-open accordion), so this SHOULD warn — the row
    // being unmounted is the dirty one.
    await user.click(screen.getByRole("button", { name: /blue/i }));
    const dialog = await screen.findByRole("dialog", {
      name: "Unsaved changes",
    });
    await user.click(
      within(dialog).getByRole("button", { name: /discard changes/i }),
    );

    // Now Blue is open and clean. Collapsing it must NOT warn, even though it was Red that
    // was dirty a moment ago — Red's registration went with its unmount.
    await user.click(screen.getByRole("button", { name: /blue/i }));
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Unsaved changes" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("warns before Show less hides a dirty row", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 7 }, (_, i) => variant(20 + i, `V${i}`));
    setRoutes(withVariants(many));
    await renderProductPage();
    await user.click(variantsTab());

    await user.click(
      await screen.findByRole("button", { name: /show all 7/i }),
    );

    // V6 is only visible while expanded — it sits past INITIAL_VARIANT_LIMIT of 5.
    await user.click(await screen.findByRole("button", { name: /^V6/i }));
    const nameInput = await screen.findByDisplayValue("V6");
    await user.clear(nameInput);
    await user.type(nameInput, "V6 edited");

    await user.click(screen.getByRole("button", { name: /show less/i }));

    const dialog = await screen.findByRole("dialog", {
      name: "Unsaved changes",
    });
    expect(within(dialog).getByText(/Variant "V6"/)).toBeInTheDocument();
  });

  it("saves a variant rename and stops warning", async () => {
    const user = userEvent.setup();
    setRoutes(withVariants(two));
    await renderProductPage();
    await user.click(variantsTab());

    await user.click(await screen.findByRole("button", { name: /red/i }));
    const nameInput = await screen.findByDisplayValue("Red");

    // The row's own Save is gone — the slide-over footer Save commits the rename.
    const footerSave = screen.getByRole("button", { name: "Save" });
    expect(footerSave).toBeDisabled();

    await user.clear(nameInput);
    await user.type(nameInput, "Crimson");
    await waitFor(() => expect(footerSave).toBeEnabled());

    await user.click(footerSave);
    await waitFor(() => expect(footerSave).toBeDisabled());
    expect(
      calls.some((c) => c.method === "PATCH" && c.path === "/variants/11"),
    ).toBe(true);
  });
});

describe("pricing paths", () => {
  it("warns before the pricing-mode select unmounts a dirty form", async () => {
    const user = userEvent.setup();
    setRoutes(withVariants([variant(11, "Red")]));
    await renderProductPage();
    await user.click(pricingTab());

    await user.type(await screen.findByLabelText("Sale price (£)"), "12.50");

    // Changing mode swaps the whole deferred form out.
    await user.selectOptions(screen.getByLabelText("Pricing mode"), "line");

    const dialog = await screen.findByRole("dialog", {
      name: "Unsaved changes",
    });
    expect(within(dialog).getByText("Pricing")).toBeInTheDocument();

    await user.click(
      within(dialog).getByRole("button", { name: /keep editing/i }),
    );
    expect(
      calls.some((c) => c.method === "PATCH" && c.path === "/products/1"),
    ).toBe(false);
  });

  it("warns before Show less hides a dirty line-price row", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 7 }, (_, i) =>
      variant(30 + i, `L${i}`, { sale_price: null }),
    );
    setRoutes(withVariants(many, { ...PRODUCT, pricing_mode: "line" }));
    await renderProductPage();
    await user.click(pricingTab());

    await user.click(
      await screen.findByRole("button", { name: /show all 7/i }),
    );

    // Row 7 is past INITIAL_LINE_LIMIT, so Show less unmounts it.
    const priceInputs = await screen.findAllByLabelText("Sale price (£)");
    await user.type(priceInputs[6], "9.99");

    await user.click(screen.getByRole("button", { name: /show less/i }));

    const dialog = await screen.findByRole("dialog", {
      name: "Unsaved changes",
    });
    expect(within(dialog).getByText(/Pricing — L6/)).toBeInTheDocument();
  });

  it("warns before the vary-by select re-groups a dirty variable-price form", async () => {
    // Changing the attribute re-keys the group rows, unmounting whichever was being edited.
    const user = userEvent.setup();
    const variable = {
      ...PRODUCT,
      pricing_mode: "variable",
      pricing_variable_attribute: 1,
      variant_attribute1_name: "Colour",
      variant_attribute2_name: "Size",
    };
    setRoutes(
      withVariants([variant(11, "Red"), variant(12, "Blue")], variable),
    );
    await renderProductPage();
    await user.click(pricingTab());

    const groupPrice = (await screen.findAllByLabelText("Sale price (£)"))[0];
    await user.clear(groupPrice);
    await user.type(groupPrice, "15.00");

    await user.selectOptions(screen.getByLabelText("Vary by"), "2");

    const dialog = await screen.findByRole("dialog", {
      name: "Unsaved changes",
    });
    expect(within(dialog).getByText(/^Pricing — /)).toBeInTheDocument();

    await user.click(
      within(dialog).getByRole("button", { name: /keep editing/i }),
    );
    expect(
      calls.some((c) => c.method === "PATCH" && c.path === "/products/1"),
    ).toBe(false);
  });
});

describe("bundle toggle", () => {
  it("warns before the bundle toggle rewrites the tabs under a dirty editor", async () => {
    const user = userEvent.setup();
    await renderProductPage();
    await user.click(bomTab());

    const qty = await buildQtyInput();
    await user.clear(qty);
    await user.type(qty, "9");

    await user.click(
      screen.getByRole("checkbox", { name: /this is a bundle/i }),
    );

    const dialog = await screen.findByRole("dialog", {
      name: "Unsaved changes",
    });
    expect(within(dialog).getByText("Build BOM")).toBeInTheDocument();

    await user.click(
      within(dialog).getByRole("button", { name: /keep editing/i }),
    );
    expect(
      calls.some((c) => c.method === "PATCH" && c.path === "/products/1"),
    ).toBe(false);
  });
});

describe("arriving from the dashboard's Build now", () => {
  // Both the build form and the stock-adjustment form label a select "Variant"; the build
  // form is the first one rendered.
  const buildVariantSelect = async () =>
    (await screen.findAllByLabelText("Variant"))[0];

  it("opens the Stock tab with the ordered variant already chosen", async () => {
    setRoutes(withVariants([variant(2, "Red"), variant(3, "Blue")]));
    await renderProductPage("/products/1?tab=stock&variantId=3");

    expect(
      await screen.findByRole("heading", { name: "Record a build" }),
    ).toBeInTheDocument();
    expect(await buildVariantSelect()).toHaveValue("3");
  });

  it("leaves the variant unchosen when the id isn't one of this product's", async () => {
    // A stale link, or a hand-edited URL. Falling back to the empty form is right: the
    // select is `required`, so the user is asked rather than silently given the wrong one.
    setRoutes(withVariants([variant(2, "Red")]));
    await renderProductPage("/products/1?tab=stock&variantId=999");

    expect(await buildVariantSelect()).toHaveValue("");
  });

  it("leaves the variant unchosen when the link carries none", async () => {
    setRoutes(withVariants([variant(2, "Red")]));
    await renderProductPage("/products/1?tab=stock");

    expect(await buildVariantSelect()).toHaveValue("");
  });

  it("falls back to Details when the product turns out to be a bundle", async () => {
    // get_orders_awaiting_inventory doesn't exclude bundles, so the dashboard can aim
    // ?tab=stock at one — and a bundle has no Stock tab to land on.
    setRoutes(baseRoutes({ ...PRODUCT, is_bundle: true }));
    await renderProductPage("/products/1?tab=stock&variantId=3");

    expect(await screen.findByLabelText("SKU")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Stock" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Record a build" }),
    ).not.toBeInTheDocument();
  });
});
