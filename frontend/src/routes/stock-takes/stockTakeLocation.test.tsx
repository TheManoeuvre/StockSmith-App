import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FakeRoute } from "../../test/fakeBackend";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://test", sharedPassword: "pw" }),
  saveSettings: () => Promise.resolve(),
  pickFile: () => Promise.resolve(null),
  saveFileTo: () => Promise.resolve(null),
  restartApp: () => Promise.resolve(),
  backendHostname: () => Promise.resolve("127.0.0.1"),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { routeTree } = await import("../../routeTree.gen");

const WAIT = { timeout: 5000 };

const loc = (id: number, name: string, parent: number | null) => ({
  id,
  parent_id: parent,
  name,
  code: `LOC-${String(id).padStart(6, "0")}`,
  notes: null,
  archived: false,
  own_items: 0,
  rolled_up_items: 0,
  needs_attention: false,
});
const TREE = {
  locations: [loc(1, "Workshop", null), loc(2, "Shelf", 1)],
  total_locations: 2,
  attention_locations: 0,
  empty_locations: 0,
  unplaced_items: 0,
  placeable_items: 2,
};

const line = (id: number, o: Record<string, unknown> = {}) => ({
  id,
  material_id: id,
  product_id: null,
  variant_id: null,
  location_id: 2,
  location_label: "Workshop › Shelf",
  found: false,
  name: `Item ${id}`,
  unit: "g",
  section: "Location",
  group: "Workshop › Shelf",
  subgroup: "",
  expected_qty: "10",
  allocated_qty_at_start: null,
  counted_qty: null,
  unmoved_since: null,
  notes: null,
  status: "pending",
  system_qty_at_approval: null,
  conflict_reason: null,
  delta: null,
  unit_cost: null,
  ...o,
});

const take = (lines: object[], o: Record<string, unknown> = {}) => ({
  id: 5,
  status: "open",
  includes_materials: true,
  includes_products: true,
  overdue_only: false,
  location_id: 1,
  scope_description: "All materials and all finished stock, in Workshop",
  started_at: "2026-11-25T10:00:00Z",
  closed_at: null,
  notes: null,
  open_days: 0,
  progress_status: "open",
  line_count: lines.length,
  counted_count: 0,
  completed_count: 0,
  pending_count: lines.length,
  conflict_count: 0,
  lines,
  ...o,
});

function renderAt(url: string, routes: FakeRoute[]) {
  setRoutes([
    { method: "GET", path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET", path: "/locations", respond: () => TREE },
    { method: "GET", path: "/dashboard/summary", respond: () => ({}) },
    { method: "GET", path: "/product-categories", respond: () => [] },
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

describe("Starting a stock take by location", () => {
  const PREVIEW = {
    candidate_count: 2,
    material_count: 2,
    product_count: 0,
    scope_description: "All materials, in Workshop",
    warnings: [],
  };

  it("coming from a location opens the start form with that place chosen and counts everything in it", async () => {
    renderAt("/stock-takes?location=1", [
      { method: "GET", path: "/stock-takes", respond: () => [] },
      { method: "POST", path: "/stock-takes/preview-scope", respond: () => PREVIEW },
      { method: "POST", path: "/stock-takes", respond: () => ({ stock_take: take([line(1)]), warnings: [] }) },
      { method: "GET", path: "/stock-takes/5", respond: () => take([line(1)]) },
    ]);
    const picker = await screen.findByRole("button", { name: "Count only this location" }, WAIT);
    expect(picker).toHaveTextContent("Workshop");
    // Not limited to what is due: you are about to walk this place.
    expect(screen.getByLabelText("Only what's due for counting")).not.toBeChecked();

    fireEvent.click(await screen.findByRole("button", { name: /Start counting/ }, WAIT));
    await screen.findByRole("heading", { name: /Stock take #5/ }, WAIT);
    const create = calls.find((c) => c.method === "POST" && c.path === "/stock-takes");
    expect(create?.body).toMatchObject({ location_id: 1, include_materials: true, overdue_only: false });
  });

  it("an ordinary start has no place chosen", async () => {
    renderAt("/stock-takes", [
      { method: "GET", path: "/stock-takes", respond: () => [] },
      { method: "POST", path: "/stock-takes/preview-scope", respond: () => PREVIEW },
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "Start stock take" }, WAIT));
    const picker = await screen.findByRole("button", { name: "Count only this location" }, WAIT);
    expect(picker).toHaveTextContent("Anywhere");
  });
});

describe("A by-location sheet", () => {
  it("is grouped by place, tags items found there, and offers to add one", async () => {
    renderAt("/stock-takes/5", [
      { method: "GET", path: "/stock-takes", respond: () => [] },
      {
        method: "GET",
        path: "/stock-takes/5",
        respond: () => take([line(1), line(2, { found: true, expected_qty: "0" }), line(3, { location_id: 1, location_label: "Workshop", group: "Workshop" })]),
      },
    ]);
    expect(await screen.findByRole("heading", { name: /Stock take #5/ }, WAIT)).toBeInTheDocument();
    expect(screen.getAllByText("Workshop › Shelf").length).toBeGreaterThan(0);
    expect(screen.getByText("found here")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add an item found here" })).toBeInTheDocument();
  });

  it("an ordinary take has no 'found here' button", async () => {
    renderAt("/stock-takes/5", [
      { method: "GET", path: "/stock-takes", respond: () => [] },
      { method: "GET", path: "/stock-takes/5", respond: () => take([line(1, { location_id: null, section: "Materials", group: "resin" })], { location_id: null }) },
    ]);
    await screen.findByRole("heading", { name: /Stock take #5/ }, WAIT);
    expect(screen.queryByRole("button", { name: "Add an item found here" })).not.toBeInTheDocument();
  });
});

describe("Count on the Locations page", () => {
  const detail = {
    ...loc(1, "Workshop", null),
    path: [{ id: 1, name: "Workshop" }],
    contents: [],
    children: [],
    history: [],
    history_count: 0,
  };
  const base: FakeRoute[] = [{ method: "GET", path: /^\/locations\/\d+$/, respond: () => detail }];

  it("opens the stock take form for that place", async () => {
    renderAt("/locations?loc=1", [
      ...base,
      { method: "GET", path: "/stock-takes", respond: () => [] },
      { method: "POST", path: "/stock-takes/preview-scope", respond: () => ({ candidate_count: 0, material_count: 0, product_count: 0, scope_description: "x", warnings: [] }) },
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "Count" }, WAIT));
    expect(await screen.findByRole("button", { name: "Count only this location" }, WAIT)).toHaveTextContent("Workshop");
  });

  it("says Continue count and goes to the take when one is already open for it", async () => {
    renderAt("/locations?loc=1", [
      ...base,
      { method: "GET", path: "/stock-takes", respond: () => [take([line(1)])] },
      { method: "GET", path: "/stock-takes/5", respond: () => take([line(1)]) },
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "Continue count" }, WAIT));
    await waitFor(() => expect(calls.some((c) => c.path === "/stock-takes/5")).toBe(true));
    expect(await screen.findByRole("heading", { name: /Stock take #5/ }, WAIT)).toBeInTheDocument();
  });
});
