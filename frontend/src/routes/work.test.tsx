import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", async () => (await import("../test/fakeBackend")).clientMock());
vi.mock("../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
}));

const { setRoutes, calls } = await import("../test/fakeBackend");
const { routeTree } = await import("../routeTree.gen");

function card(over: Record<string, unknown> = {}) {
  return {
    key: "stage:1:1:1",
    stage_id: 7,
    stage_name: "Pre-clean",
    stage_kind: "custom",
    station: "Bench",
    product_id: 1,
    product_name: "Ceramic coaster",
    parts: [{ variant_id: 11, variant_name: "6 pack", qty: 6, finished_units: 1 }],
    qty: 6,
    unit_label: "Blanks",
    due: "2026-11-30T09:00:00Z",
    late: false,
    order_ids: [101],
    order_line_id: null,
    can_record: true,
    batch_note: null,
    ...over,
  };
}

function orderCard(over: Record<string, unknown> = {}) {
  return {
    order_id: 205,
    platform: "etsy",
    buyer_name: "A. Buyer",
    due: "2026-11-29T09:00:00Z",
    late: false,
    lines: [{ product_id: 1, product_name: "Ceramic coaster", variant_name: "2 pack", qty: 1, ready: true }],
    ready_lines: 1,
    total_lines: 1,
    ready: true,
    waiting_for: [],
    ...over,
  };
}

function list(over: Record<string, unknown> = {}) {
  return {
    enabled: true,
    generated_at: "2026-11-27T10:00:00Z",
    stations: [],
    ship_station: "Packing",
    cards: [],
    ship_cards: [],
    waiting_orders: [],
    ...over,
  };
}

function backend(work: unknown, extra: { path: string | RegExp; method?: "GET" | "POST"; respond: (b: unknown) => unknown }[] = []) {
  setRoutes([
    { method: "GET", path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET", path: "/work/settings", respond: () => ({ work_flow_enabled: (work as { enabled: boolean }).enabled, work_flow_enabled_at: null }) },
    { method: "GET", path: "/work/list", respond: () => work },
    ...extra.map((e) => ({ method: e.method ?? ("GET" as const), path: e.path, respond: e.respond })),
    { method: "GET", path: /.*/, respond: () => [] },
  ]);
}

async function renderWork(initial = "/work") {
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [initial] }) });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", { name: "Work" }, { timeout: 5000 });
  return router;
}

beforeEach(() => backend(list({ enabled: false })));

describe("Work page", () => {
  it("says work flow is off, and where to turn it on", async () => {
    await renderWork();
    expect(await screen.findByText("Work flow is off")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Turn it on in Settings" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Work" })).toBeNull(); // not in the sidebar until enabled
  });

  it("shows the Work link in the sidebar once enabled", async () => {
    backend(list());
    await renderWork();
    await waitFor(() => expect(screen.getByRole("link", { name: "Work" })).toBeTruthy());
  });

  it("explains an empty list", async () => {
    backend(list());
    await renderWork();
    expect(await screen.findByText("Nothing to do")).toBeTruthy();
  });

  it("marks only the most urgent task as Next and shows quantity, unit and orders", async () => {
    backend(list({ stations: [{ name: "Bench", count: 2, earliest_due: null }], cards: [card(), card({ key: "b", product_name: "Wood coaster", qty: 2, unit_label: null, parts: [{ variant_id: null, variant_name: null, qty: 2, finished_units: 2 }] })] }));
    await renderWork();
    const first = await screen.findByRole("listitem", { name: "Pre-clean: Ceramic coaster" });
    expect(within(first).getByText("Next")).toBeTruthy();
    expect(within(first).getByText("blanks")).toBeTruthy();
    expect(within(first).getByText(/for #101/)).toBeTruthy();
    const second = screen.getByRole("listitem", { name: "Pre-clean: Wood coaster" });
    expect(within(second).queryByText("Next")).toBeNull();
  });

  it("records the quantity you enter against the right product, stage and variant", async () => {
    backend(list({ cards: [card()] }), [{ method: "POST", path: "/work/progress", respond: () => [{ id: 9, product_id: 1, variant_id: 11, order_line_id: null, stage_id: 7, qty: 4, note: null, created_at: "", stage_name: null, product_name: null, variant_name: null }] }]);
    await renderWork();
    const row = await screen.findByRole("listitem", { name: "Pre-clean: Ceramic coaster" });
    const input = within(row).getByLabelText("How many Pre-clean done");
    await userEvent.clear(input);
    await userEvent.type(input, "4");
    await userEvent.click(within(row).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/work/progress")).toBe(true));
    expect(calls.find((c) => c.path === "/work/progress")?.body).toMatchObject({ product_id: 1, variant_id: 11, stage_id: 7, qty: 4 });
    expect(await screen.findByText("Recorded 4.")).toBeTruthy();
  });

  it("lets the server spread a tick on a mixed batch over its designs", async () => {
    const mixed = card({
      parts: [
        { variant_id: 11, variant_name: "Design A", qty: 3, finished_units: 3 },
        { variant_id: 12, variant_name: "Design B", qty: 3, finished_units: 3 },
      ],
      qty: 6,
      unit_label: null,
      batch_note: "Mixed batch",
    });
    backend(list({ cards: [mixed] }), [{ method: "POST", path: "/work/progress", respond: () => [] }]);
    await renderWork();
    const row = await screen.findByRole("listitem", { name: "Pre-clean: Ceramic coaster" });
    expect(within(row).getByText("3 × Design A, 3 × Design B")).toBeTruthy();
    expect(within(row).getByText("Mixed batch")).toBeTruthy();
    await userEvent.click(within(row).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/work/progress")).toBe(true));
    expect(calls.find((c) => c.path === "/work/progress")?.body).toMatchObject({ variant_id: null, qty: 6 });
  });

  it("does not offer Done on Make, which is recorded as a build", async () => {
    backend(list({ cards: [card({ stage_name: "Make", stage_kind: "make", can_record: false, unit_label: null, station: "Making", qty: 2 })] }));
    await renderWork();
    const row = await screen.findByRole("listitem", { name: "Make: Ceramic coaster" });
    expect(within(row).queryByRole("button", { name: "Done" })).toBeNull();
    const link = within(row).getByRole("link", { name: /Record build — 6 pack/ });
    expect(link.getAttribute("href")).toContain("/products/1");
    expect(link.getAttribute("href")).toContain("variantId=11");
  });

  it("filters by station", async () => {
    backend(
      list({
        stations: [
          { name: "Bench", count: 1, earliest_due: null },
          { name: "Making", count: 1, earliest_due: null },
        ],
        cards: [card(), card({ key: "m", stage_name: "Make", stage_kind: "make", station: "Making", can_record: false, product_name: "Wood coaster" })],
      }),
    );
    await renderWork();
    await screen.findByRole("listitem", { name: "Pre-clean: Ceramic coaster" });
    await userEvent.click(screen.getByRole("button", { name: "Making (1)" }));
    await waitFor(() => expect(screen.queryByRole("listitem", { name: "Pre-clean: Ceramic coaster" })).toBeNull());
    expect(screen.getByRole("listitem", { name: "Make: Wood coaster" })).toBeTruthy();
  });

  it("lists orders ready to ship and orders still waiting, with why", async () => {
    backend(
      list({
        stations: [{ name: "Packing", count: 1, earliest_due: null }],
        ship_cards: [orderCard()],
        waiting_orders: [orderCard({ order_id: 206, ready: false, ready_lines: 1, total_lines: 2, waiting_for: ["Wood coaster: still to make"] })],
      }),
    );
    await renderWork();
    expect(await screen.findByRole("heading", { name: "Ready to ship" })).toBeTruthy();
    expect(screen.getByRole("listitem", { name: "Order 205" })).toBeTruthy();
    const waiting = screen.getByRole("listitem", { name: "Order 206" });
    expect(within(waiting).getByText("1 of 2 items ready")).toBeTruthy();
    expect(within(waiting).getByText(/Waiting for: Wood coaster: still to make/)).toBeTruthy();
  });

  it("flags a late task", async () => {
    backend(list({ cards: [card({ late: true })] }));
    await renderWork();
    expect(await screen.findByText(/Late ·/)).toBeTruthy();
  });

  it("undoes what was just recorded", async () => {
    backend(list({ cards: [card()] }), [
      { method: "POST", path: "/work/progress", respond: () => [{ id: 9, product_id: 1, variant_id: 11, order_line_id: null, stage_id: 7, qty: 6, note: null, created_at: "", stage_name: null, product_name: null, variant_name: null }] },
      { method: "POST", path: "/work/progress/9/undo", respond: () => ({ id: 10, qty: -6 }) },
    ]);
    await renderWork();
    const row = await screen.findByRole("listitem", { name: "Pre-clean: Ceramic coaster" });
    await userEvent.click(within(row).getByRole("button", { name: "Done" }));
    await userEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/work/progress/9/undo")).toBe(true));
  });
});
