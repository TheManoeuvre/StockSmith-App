import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FakeRoute } from "../test/fakeBackend";

vi.mock("../api/client", async () => (await import("../test/fakeBackend")).clientMock());
vi.mock("../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
  openExternalUrl: vi.fn(),
}));

const { setRoutes } = await import("../test/fakeBackend");
const { routeTree } = await import("../routeTree.gen");

const WAIT = { timeout: 5000 };

const row = (id: number, name: string, parent: number | null, o: object = {}) => ({
  id,
  parent_id: parent,
  name,
  code: `LOC-${String(id).padStart(6, "0")}`,
  notes: null,
  archived: false,
  own_items: 0,
  rolled_up_items: 0,
  needs_attention: false,
  ...o,
});

const EMPTY_TREE = {
  locations: [],
  total_locations: 0,
  attention_locations: 0,
  empty_locations: 0,
  unplaced_items: 0,
  placeable_items: 0,
};

function renderAt(url: string, routes: FakeRoute[]) {
  setRoutes([
    { method: "GET", path: "/system/status", respond: () => ({ status: "ok" }) },
    ...routes,
    { method: "GET", path: /^\/orders\?/, respond: () => ({ items: [], total: 0, limit: 1, offset: 0 }) },
    { method: "GET", path: /.*/, respond: () => [] },
  ]);
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [url] }) });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  setRoutes([]);
  window.localStorage.clear();
});

describe("Locations page", () => {
  it("first run explains what locations are and offers both ways to start", async () => {
    renderAt("/locations", [{ method: "GET", path: "/locations", respond: () => EMPTY_TREE }]);
    expect(await screen.findByRole("heading", { name: "Where do you keep things?" }, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add a location" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate a range/ })).toBeInTheDocument();
  });

  it("offers CSV import and export, even before the first location exists", async () => {
    renderAt("/locations", [{ method: "GET", path: "/locations", respond: () => EMPTY_TREE }]);
    expect(await screen.findByRole("button", { name: "Export CSV" }, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import CSV" })).toBeInTheDocument();
  });

  it("is in the sidebar under Stock, between Purchases and Stock Take", async () => {
    renderAt("/locations", [{ method: "GET", path: "/locations", respond: () => EMPTY_TREE }]);
    await screen.findByRole("heading", { name: "Where do you keep things?" }, WAIT);
    const labels = screen.getAllByRole("link").map((l) => l.getAttribute("aria-label")).filter(Boolean);
    expect(labels.slice(labels.indexOf("Materials"), labels.indexOf("Stock Take") + 1)).toEqual([
      "Materials",
      "Purchases",
      "Locations",
      "Stock Take",
    ]);
  });

  it("with locations it shows the tree, the selected location, and its contents", async () => {
    const workshop = row(1, "Workshop", null, { rolled_up_items: 2 });
    const bin = row(2, "Bin 1", 1, { own_items: 2, rolled_up_items: 2 });
    renderAt("/locations?loc=2", [
      {
        method: "GET",
        path: "/locations",
        respond: () => ({
          locations: [workshop, bin],
          total_locations: 2,
          attention_locations: 0,
          empty_locations: 0,
          unplaced_items: 0,
          placeable_items: 2,
        }),
      },
      {
        method: "GET",
        path: "/locations/2",
        respond: () => ({
          ...bin,
          path: [
            { id: 1, name: "Workshop" },
            { id: 2, name: "Bin 1" },
          ],
          contents: [
            { placement_id: 5, material_id: 1, product_id: null, variant_id: null, name: "Lavender oil", sku: null, unit: "ml", qty: "640", is_primary: true, drift: "0" },
            { placement_id: 6, material_id: 2, product_id: null, variant_id: null, name: "Cedarwood oil", sku: null, unit: "ml", qty: "250", is_primary: false, drift: "1" },
          ],
          children: [],
          history: [],
          history_count: 0,
        }),
      },
    ]);
    expect(await screen.findByRole("heading", { name: "Bin 1" }, WAIT)).toBeInTheDocument();
    expect(screen.getByText("LOC-000002")).toBeInTheDocument();
    expect(screen.getByText("Lavender oil")).toBeInTheDocument();
    expect(screen.getByText("Overflow")).toBeInTheDocument();
    expect(screen.getByText(/Cedarwood oil:/)).toBeInTheDocument(); // the drift banner: plain and amber
    expect(screen.getByRole("button", { name: "Generate range" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Move location" }));
    expect(await screen.findByRole("dialog", { name: "Move Bin 1" })).toBeInTheDocument();
  });

  it("offers 'Place your stock' when locations exist and nothing has been placed", async () => {
    const shelf = row(1, "Shelf", null);
    renderAt("/locations", [
      {
        method: "GET",
        path: "/locations",
        respond: () => ({
          locations: [shelf, row(3, "Loft", null)],
          total_locations: 2,
          attention_locations: 0,
          empty_locations: 2,
          unplaced_items: 38,
          placeable_items: 38,
        }),
      },
      {
        method: "GET",
        path: /^\/locations\/\d+$/,
        respond: () => ({ ...shelf, path: [{ id: 1, name: "Shelf" }], contents: [], children: [], history: [], history_count: 0 }),
      },
    ]);
    expect(await screen.findByRole("heading", { name: "Place your stock" }, WAIT)).toBeInTheDocument();
    expect(screen.getByText("38 items unplaced")).toBeInTheDocument();
  });
});
