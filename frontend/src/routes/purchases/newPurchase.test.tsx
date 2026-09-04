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

// PETG is healthy and first, so "+ Add line" grabs it; PLA is the one that should surface
// in Stock alerts.
function materials() {
  return [
    {
      id: 8,
      name: "PETG Clear",
      unit: "g",
      category: "filament",
      colour_hex: "#88ccee",
      current_qty: "5000",
      reorder_threshold: "0",
      avg_unit_cost: "0.0100",
      consumption_rate_per_week: "50",
      on_order_qty: "0",
      typical_reorder_qty: null,
      lead_time_days: 5,
      stockout_status: "ok",
      weeks_of_supply: "40",
      default_supplier_id: 3,
      default_supplier_name: "Polymax",
    },
    {
      id: 7,
      name: "PLA Black",
      unit: "g",
      category: "filament",
      colour_hex: "#111111",
      current_qty: "200",
      reorder_threshold: "1000",
      avg_unit_cost: "0.0200",
      consumption_rate_per_week: "100",
      on_order_qty: "0",
      typical_reorder_qty: "800",
      lead_time_days: 5,
      stockout_status: "warning",
      weeks_of_supply: "2",
      default_supplier_id: 3,
      default_supplier_name: "Polymax",
    },
  ];
}

function created(body: unknown) {
  return {
    id: 9,
    supplier_id: 3,
    supplier_name: "Polymax",
    supplier_order_number: null,
    order_date: "2026-09-04",
    expected_arrival_date: null,
    status: "ordered",
    received_at: null,
    notes: null,
    delivery_cost: (body as { delivery_cost?: string | null }).delivery_cost ?? null,
    created_at: "2026-09-04",
    updated_at: "2026-09-04",
    lines: [],
  };
}

function routes() {
  return [
    { method: "GET" as const, path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET" as const, path: "/materials", respond: () => materials() },
    { method: "GET" as const, path: "/suppliers", respond: () => [{ id: 3, name: "Polymax" }] },
    {
      method: "POST" as const,
      path: "/purchases/price-reference",
      respond: () => [
        {
          material_id: 8,
          unit_cost: "0.0100",
          qty: "1000",
          total_cost: "10.00",
          supplier_id: 3,
          supplier_name: "Polymax",
          purchase_id: 2,
          purchase_ref: "PO-2",
          at: "2026-08-01",
          same_supplier: true,
        },
        {
          material_id: 7,
          unit_cost: "0.0200",
          qty: "500",
          total_cost: "10.00",
          supplier_id: 3,
          supplier_name: "Polymax",
          purchase_id: 1,
          purchase_ref: "PO-1",
          at: "2026-07-01",
          same_supplier: true,
        },
      ],
    },
    {
      method: "POST" as const,
      path: "/purchases",
      respond: (b: unknown) => created(b),
    },
    { method: "GET" as const, path: "/purchases/9", respond: () => created({}) },
    { method: "GET" as const, path: /^\/purchases(\?|$)/, respond: () => [] },
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ];
}

async function renderNew() {
  setRoutes(routes());
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ["/purchases/new"] }),
  });
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", { name: "New purchase" }, { timeout: 5000 });
  return router;
}

beforeEach(() => setRoutes([]));

it("blocks 'Raise purchase order' until there's a complete line", async () => {
  await renderNew();

  const raise = screen.getByRole("button", { name: "Raise purchase order" });
  expect(raise).toBeDisabled();
  expect(raise).toHaveAttribute("title", "Add at least one order line");
  // Surfaced in the footer and the header status row, not only via the greyed button.
  expect(
    screen.getAllByText("Add at least one order line").length,
  ).toBeGreaterThanOrEqual(2);
});

it("enables the action once a line has a quantity and a total", async () => {
  const user = userEvent.setup();
  await renderNew();

  await user.click(screen.getByRole("button", { name: "+ Add line" }));
  await user.type(screen.getByLabelText("Quantity"), "1000");
  await user.type(screen.getByLabelText("Line total"), "20");

  const raise = screen.getByRole("button", { name: "Raise purchase order" });
  await waitFor(() => expect(raise).toBeEnabled());
  expect(screen.getByText("Ready to raise purchase order")).toBeInTheDocument();
});

it("raises the order and opens the new purchase's detail panel", async () => {
  const user = userEvent.setup();
  const router = await renderNew();

  await user.click(screen.getByRole("button", { name: "+ Add line" }));
  await user.type(screen.getByLabelText("Quantity"), "1000");
  await user.type(screen.getByLabelText("Line total"), "20");
  await user.type(screen.getByLabelText("Delivery and carriage"), "4.5");

  await user.click(screen.getByRole("button", { name: "Raise purchase order" }));

  await waitFor(() => {
    const post = calls.find((c) => c.method === "POST" && c.path === "/purchases");
    expect(post).toBeTruthy();
    const body = post!.body as { delivery_cost?: string; lines: unknown[] };
    expect(body.delivery_cost).toBe("4.5");
    expect(body.lines).toHaveLength(1);
  });
  await waitFor(() =>
    expect(router.state.location.pathname).toBe("/purchases/9"),
  );
});

it("flags a line priced above what the supplier last charged", async () => {
  const user = userEvent.setup();
  await renderNew();

  await user.click(screen.getByRole("button", { name: "+ Add line" }));
  await user.type(screen.getByLabelText("Quantity"), "1000");
  await user.type(screen.getByLabelText("Line total"), "20"); // £0.02/g vs £0.01 last

  // Stat sub-line switches to the "above last price" wording.
  expect(await screen.findByText("1 line above last price")).toBeInTheDocument();
});

it("adds a recommended material from Stock alerts", async () => {
  const user = userEvent.setup();
  await renderNew();

  // PLA is below threshold and not on a line yet.
  const addButtons = await screen.findAllByRole("button", { name: "Add" });
  await user.click(addButtons[0]);

  await waitFor(() =>
    expect(screen.getByLabelText("Quantity")).toHaveValue(800),
  );
});

it("Discard returns to the list without a request", async () => {
  const user = userEvent.setup();
  const router = await renderNew();

  await user.click(screen.getByRole("button", { name: "Discard" }));

  await waitFor(() => expect(router.state.location.pathname).toBe("/purchases"));
  expect(calls.some((c) => c.method === "POST" && c.path === "/purchases")).toBe(false);
});
