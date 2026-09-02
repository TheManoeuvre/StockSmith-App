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

function product(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    name: "Doorbell Mount",
    sku: "DM-1",
    is_bundle: false,
    is_active: true,
    current_stock: 5,
    allocated_qty: 0,
    sale_price: "12.99",
    cost_per_unit: "0.59",
    kitting_cost_per_unit: "0.23",
    cost_per_unit_min: null,
    cost_per_unit_max: null,
    kitting_cost_per_unit_min: null,
    kitting_cost_per_unit_max: null,
    effective_shipping_profile_id: 1,
    effective_shipping_profile_name: "Small Parcel 48",
    effective_shipping_cost: "3.65",
    cogs_incomplete: false,
    pricing_mode: "product",
    push_buildable_capacity: true,
    made_to_order: false,
    platform_ceiling_qty: null,
    shipping_profile_id: 1,
    platform_fee_percent: null,
    effective_platform_fee_percent: null,
    pricing_variable_attribute: null,
    product_category_name: null,
    max_sellable: 5,
    theoretical_max_sellable: 5,
    // Null short-circuits useAssetUrl — otherwise every row tries to fetch a thumbnail
    // through the Tauri bridge, which isn't available under vitest.
    main_image_asset_id: null,
    classification: null,
    ...overrides,
  };
}

async function renderList(page: Record<string, unknown>) {
  setRoutes([
    { method: "GET", path: /^\/products\?/, respond: () => page },
    { method: "GET", path: "/product-categories", respond: () => [] },
    { method: "GET", path: /all-sync-status$/, respond: () => ({}) },
    { method: "GET", path: "/system/status", respond: () => ({ status: "ok" }) },
    // Catch-all last, same as productDetail.test.tsx: anything else the layout fetches
    // answers with an empty list rather than failing the render for an unrelated reason.
    { method: "GET", path: /.*/, respond: () => [] },
  ]);
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: ["/products"] }) });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  await screen.findByText("Doorbell Mount");
}

beforeEach(() => {
  setRoutes([]);
});

it("stacks the three cost-of-goods deductions in one cell", async () => {
  await renderList({ items: [product()], total: 1, incomplete_total: 0 });

  expect(screen.getByText("£12.99")).toBeInTheDocument();
  expect(screen.getByText("£0.59")).toBeInTheDocument();
  expect(screen.getByText("£0.23")).toBeInTheDocument();
  expect(screen.getByText("£3.65")).toBeInTheDocument();
  expect(screen.getByText("Small Parcel 48")).toBeInTheDocument();
});

it("shows a variant product's spread rather than a figure no variant actually has", async () => {
  // The base BOM resolves to £0.59, but the variants that ship cost between £0.59 and
  // £1.04. Showing the base figure alone is what made this column misleading.
  await renderList({
    items: [product({ cost_per_unit_min: "0.59", cost_per_unit_max: "1.04" })],
    total: 1,
    incomplete_total: 0,
  });

  expect(screen.getByText("£0.59 – £1.04")).toBeInTheDocument();
});

it("collapses a range whose ends match to a single figure", async () => {
  await renderList({
    items: [product({ cost_per_unit_min: "0.59", cost_per_unit_max: "0.59" })],
    total: 1,
    incomplete_total: 0,
  });

  expect(screen.getByText("£0.59")).toBeInTheDocument();
  expect(screen.queryByText("£0.59 – £0.59")).not.toBeInTheDocument();
});

it("names what is missing instead of showing a bare dash", async () => {
  await renderList({
    items: [
      product({
        effective_shipping_profile_id: null,
        effective_shipping_profile_name: null,
        effective_shipping_cost: null,
        cogs_incomplete: true,
      }),
    ],
    total: 1,
    incomplete_total: 1,
  });

  // "— no profile" says where to go; a dash on its own reads as "nothing to show".
  expect(screen.getByText(/no profile/)).toBeInTheDocument();
});

it("leaves a product with no packaging alone", async () => {
  // Null kitting means "no packaging", not "packaging is free" — a legitimate setup, so it
  // must not be dressed up as a gap the way a missing shipping profile is.
  await renderList({
    items: [product({ kitting_cost_per_unit: null })],
    total: 1,
    incomplete_total: 0,
  });

  expect(screen.queryByText(/no BOM/)).not.toBeInTheDocument();
  expect(screen.queryByText(/no profile/)).not.toBeInTheDocument();
});

it("announces the gap count on the Cost gaps tab without it having to be switched on first", async () => {
  await renderList({ items: [product()], total: 1, incomplete_total: 6 });

  expect(screen.getByRole("button", { name: /cost gaps/i })).toHaveTextContent("6");
});

it("hides the Cost gaps tab entirely when there is nothing to act on", async () => {
  // A permanent "0" is what teaches people to stop reading a counter.
  await renderList({ items: [product()], total: 1, incomplete_total: 0 });

  expect(screen.queryByRole("button", { name: /cost gaps/i })).not.toBeInTheDocument();
});

it("asks the server to narrow the list, not the current page", async () => {
  await renderList({ items: [product()], total: 1, incomplete_total: 6 });

  await userEvent.click(screen.getByRole("button", { name: /cost gaps/i }));

  // Server-side: the list is paginated, so a client-side filter would narrow one page and
  // leave the total wrong.
  await waitFor(() => expect(calls.some((c) => c.path.includes("cogs_incomplete=true"))).toBe(true));
});

it("sends the search term to the server", async () => {
  await renderList({ items: [product()], total: 1, incomplete_total: 0 });

  await userEvent.type(screen.getByPlaceholderText("Search name, SKU…"), "doorbell");

  // Debounced into the query key, then a real request — search has to reach the server
  // because the list is paginated.
  await waitFor(() => expect(calls.some((c) => c.path.includes("q=doorbell"))).toBe(true));
});
