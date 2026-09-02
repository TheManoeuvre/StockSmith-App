import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { routeTree } = await import("../../routeTree.gen");

const MATERIAL = {
  id: 7,
  name: "PLA+ Filament",
  category: "filament",
  category_id: 1,
  unit: "g",
  colour: "Black",
  material_type_id: null,
  material_type_name: null,
  barcode: null,
  manufacturer_id: null,
  manufacturer_name: null,
  product_url: null,
  default_supplier_id: null,
  default_supplier_name: null,
  typical_reorder_qty: null,
  reorder_threshold: "100",
  current_qty: "2500",
  allocated_qty: "0",
  avg_unit_cost: "0.024",
  on_order_qty: "0",
  is_active: true,
  image_asset_id: null,
};

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

function materialRoutes(material: Record<string, unknown> = MATERIAL) {
  return [
    { method: "GET" as const, path: "/materials/7", respond: () => material },
    { method: "PATCH" as const, path: "/materials/7", respond: (body: unknown) => ({ ...material, ...(body as object) }) },
    { method: "GET" as const, path: /^\/materials\/7\/stock-history/, respond: () => [] },
    { method: "GET" as const, path: "/materials", respond: () => [material] },
    { method: "GET" as const, path: "/material-categories", respond: () => CATEGORIES },
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ];
}

async function renderMaterialPage(path = "/materials/7") {
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [path] }) });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      <RouterProvider router={router as any} />
    </QueryClientProvider>
  );
  return router;
}

describe("material detail", () => {
  beforeEach(() => setRoutes(materialRoutes()));

  it("disables Save until a detail changes, then re-disables after saving", async () => {
    const user = userEvent.setup();
    await renderMaterialPage();

    // The panel opens on the Stock tab now — the editable identity fields are under Details.
    await user.click(await screen.findByRole("button", { name: "Details" }, { timeout: 5000 }));
    const nameInput = await screen.findByDisplayValue("PLA+ Filament", {}, { timeout: 5000 });
    const save = within(nameInput.closest("form")!).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    await user.type(nameInput, " v2");
    await waitFor(() => expect(save).toBeEnabled());

    await user.click(save);
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.path === "/materials/7")).toBe(true));
    await waitFor(() => expect(save).toBeDisabled());
  });

  it("warns before navigating away from unsaved detail edits", async () => {
    const user = userEvent.setup();
    const router = await renderMaterialPage();

    await user.click(await screen.findByRole("button", { name: "Details" }, { timeout: 5000 }));
    const nameInput = await screen.findByDisplayValue("PLA+ Filament", {}, { timeout: 5000 });
    await user.type(nameInput, " v2");

    await user.click(screen.getByRole("link", { name: "Products" }));

    // Named lookup, not a bare role query: the detail panel itself is also role="dialog"
    // (see DetailPanel.tsx) and stays mounted underneath, so an unqualified query would be
    // ambiguous between it and this confirmation.
    const dialog = await screen.findByRole("dialog", { name: "Unsaved changes" });
    expect(within(dialog).getByText("Material details")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /keep editing/i }));
    expect(router.state.location.pathname).toBe("/materials/7");
  });

  it("gates the stock adjustment on having a value and a reason", async () => {
    const user = userEvent.setup();
    await renderMaterialPage();

    // The panel opens on the Stock tab, which holds the adjust form.
    const reason = await screen.findByLabelText("Reason", {}, { timeout: 5000 });
    const adjustForm = reason.closest("form")!;
    const record = within(adjustForm).getByRole("button", { name: "Save" });
    expect(record).toBeDisabled();

    await user.type(within(adjustForm).getByLabelText("Adjust by"), "-5");
    expect(record).toBeDisabled(); // a value alone isn't enough

    await user.selectOptions(reason, "Correction");
    await waitFor(() => expect(record).toBeEnabled());
  });

  it("warns about a half-typed stock adjustment", async () => {
    const user = userEvent.setup();
    const router = await renderMaterialPage();

    const reason = await screen.findByLabelText("Reason", {}, { timeout: 5000 });
    await user.selectOptions(reason, "Spool ran short");

    await user.click(screen.getByRole("link", { name: "Products" }));

    const dialog = await screen.findByRole("dialog", { name: "Unsaved changes" });
    expect(within(dialog).getByText("Stock adjustment")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /discard changes/i }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/products"));
  });

  it("does not warn when nothing has been touched", async () => {
    const user = userEvent.setup();
    const router = await renderMaterialPage();
    await screen.findByRole("dialog", { name: "PLA+ Filament" }, { timeout: 5000 });

    await user.click(screen.getByRole("link", { name: "Products" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/products"));
    expect(screen.queryByRole("dialog", { name: "Unsaved changes" })).not.toBeInTheDocument();
  });
});

describe("materials list", () => {
  beforeEach(() => setRoutes(materialRoutes()));

  it("warns before collapsing the new-material form over typed input", async () => {
    const user = userEvent.setup();
    await renderMaterialPage("/materials");

    const addButton = await screen.findByRole("button", { name: /add material/i }, { timeout: 5000 });
    await user.click(addButton);

    const nameInput = await screen.findByLabelText("Name");
    expect(within(nameInput.closest("form")!).getByRole("button", { name: "Save" })).toBeDisabled();

    await user.type(nameInput, "New filament");
    await waitFor(() =>
      expect(within(nameInput.closest("form")!).getByRole("button", { name: "Save" })).toBeEnabled()
    );

    await user.click(addButton);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("New material")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /keep editing/i }));
    expect(screen.getByDisplayValue("New filament")).toBeInTheDocument();
  });
});

describe("category-driven fields", () => {
  it("offers Colour and Material type when the category tracks them", async () => {
    const user = userEvent.setup();
    setRoutes(materialRoutes());
    await renderMaterialPage();

    await user.click(await screen.findByRole("button", { name: "Details" }, { timeout: 5000 }));
    await screen.findByDisplayValue("PLA+ Filament", {}, { timeout: 5000 });
    expect(screen.getByText("Colour")).toBeInTheDocument();
    expect(screen.getByText("Material type")).toBeInTheDocument();
  });

  it("hides them for a category that doesn't, and shows cost per unit", async () => {
    // Same page, same material, different category row — nothing here names filament, which is
    // the point: the fields follow the flags, not a hardcoded category.
    const user = userEvent.setup();
    setRoutes(materialRoutes({ ...MATERIAL, category: "packaging", category_id: 2, unit: "each" }));
    await renderMaterialPage();

    const panel = await screen.findByRole("dialog", { name: "PLA+ Filament" }, { timeout: 5000 });
    await user.click(within(panel).getByRole("button", { name: "Details" }));
    await screen.findByDisplayValue("PLA+ Filament", {}, { timeout: 5000 });
    expect(within(panel).queryByText("Colour")).toBeNull();
    expect(within(panel).queryByText("Material type")).toBeNull();

    // The read-only cost figure lives on the Supplier tab now.
    await user.click(within(panel).getByRole("button", { name: "Supplier" }));
    expect(within(panel).getByText("Avg unit cost")).toBeInTheDocument();
    expect(within(panel).queryByText("Avg cost/kg")).toBeNull();
  });

  it("shows cost per kg for a category that asks for it", async () => {
    const user = userEvent.setup();
    setRoutes(materialRoutes());
    await renderMaterialPage();

    await user.click(await screen.findByRole("button", { name: "Supplier" }, { timeout: 5000 }));
    expect(await screen.findByText("Avg cost/kg")).toBeInTheDocument();
  });
});
