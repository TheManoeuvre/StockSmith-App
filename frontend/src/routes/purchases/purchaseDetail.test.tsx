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

function purchase(over: Record<string, unknown> = {}) {
  return {
    id: 5,
    supplier_id: 3,
    supplier_name: "Polymax",
    supplier_order_number: null,
    order_date: "2026-08-21",
    expected_arrival_date: "2026-08-27",
    status: "ordered",
    received_at: null,
    notes: null,
    delivery_cost: null,
    created_at: "2026-08-21",
    updated_at: "2026-08-21",
    lines: [
      {
        id: 1,
        purchase_id: 5,
        material_id: 7,
        qty: "1000",
        total_cost: "20.00",
        notes: null,
        closed_at: null,
        received_qty: "0",
        outstanding_qty: "1000",
        receipts: [],
      },
    ],
    ...over,
  };
}

function routes(over: Record<string, unknown> = {}) {
  return [
    { method: "GET" as const, path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET" as const, path: "/purchases/5", respond: () => purchase(over) },
    {
      method: "PATCH" as const,
      path: "/purchases/5",
      respond: (b: unknown) => purchase({ ...over, ...(b as object) }),
    },
    { method: "PUT" as const, path: "/purchases/5/lines", respond: () => purchase(over) },
    { method: "GET" as const, path: "/materials", respond: () => [{ id: 7, name: "PLA Black", unit: "g", category: "filament" }] },
    { method: "GET" as const, path: "/suppliers", respond: () => [{ id: 3, name: "Polymax" }] },
    { method: "GET" as const, path: /^\/purchases(\?|$)/, respond: () => [purchase(over)] },
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ];
}

async function renderDetail(over: Record<string, unknown> = {}) {
  setRoutes(routes(over));
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: ["/purchases/5"] }),
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", { name: "Purchase #5" }, { timeout: 5000 });
  return router;
}

beforeEach(() => setRoutes([]));

it("shows the stat tiles and both tabs", async () => {
  await renderDetail();
  expect(await screen.findByText("Order total")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Lines" })).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Receiving history" }),
  ).toBeInTheDocument();
});

it("commits a header edit through the one footer Save", async () => {
  const user = userEvent.setup();
  await renderDetail();

  const orderDate = await screen.findByDisplayValue("2026-08-21");
  await user.clear(orderDate);
  await user.type(orderDate, "2026-08-25");

  const save = screen.getByRole("button", { name: "Save" });
  await waitFor(() => expect(save).toBeEnabled());
  await user.click(save);

  await waitFor(() =>
    expect(
      calls.some((c) => c.method === "PATCH" && c.path === "/purchases/5"),
    ).toBe(true),
  );
});

it("records the supplier's order number through the footer Save", async () => {
  const user = userEvent.setup();
  await renderDetail();

  const field = await screen.findByPlaceholderText("Their PO / order number");
  await user.type(field, "  PO-4521  ");

  const save = screen.getByRole("button", { name: "Save" });
  await waitFor(() => expect(save).toBeEnabled());
  await user.click(save);

  await waitFor(() =>
    expect(
      calls.some(
        (c) =>
          c.method === "PATCH" &&
          c.path === "/purchases/5" &&
          (c.body as { supplier_order_number?: string }).supplier_order_number ===
            "PO-4521",
      ),
    ).toBe(true),
  );
});

it("folds a recorded delivery cost into the order total", async () => {
  await renderDetail({ delivery_cost: "5.00" });
  // Line total £20.00 + £5.00 delivery.
  expect(await screen.findByText("£25.00")).toBeInTheDocument();
  expect(await screen.findByText("incl £5.00 delivery")).toBeInTheDocument();
});

it("saves an edited delivery cost through the footer Save", async () => {
  const user = userEvent.setup();
  await renderDetail();

  const field = await screen.findByPlaceholderText("0.00");
  await user.type(field, "7.5");

  const save = screen.getByRole("button", { name: "Save" });
  await waitFor(() => expect(save).toBeEnabled());
  await user.click(save);

  await waitFor(() =>
    expect(
      calls.some(
        (c) =>
          c.method === "PATCH" &&
          c.path === "/purchases/5" &&
          (c.body as { delivery_cost?: string }).delivery_cost === "7.5",
      ),
    ).toBe(true),
  );
});

it("shows an existing supplier order number in the header", async () => {
  await renderDetail({ supplier_order_number: "PO-4521" });
  expect(await screen.findByText(/Order PO-4521/)).toBeInTheDocument();
  expect(
    (await screen.findByPlaceholderText("Their PO / order number")) as HTMLInputElement,
  ).toHaveValue("PO-4521");
});

it("the footer Revert drops a header edit with no request", async () => {
  const user = userEvent.setup();
  await renderDetail();

  const orderDate = await screen.findByDisplayValue("2026-08-21");
  await user.clear(orderDate);
  await user.type(orderDate, "2026-08-25");

  await user.click(screen.getByRole("button", { name: "Revert" }));

  await waitFor(() => expect(screen.getByText("No changes")).toBeInTheDocument());
  expect(calls.some((c) => c.method === "PATCH")).toBe(false);
});

it("asks before deleting", async () => {
  const user = userEvent.setup();
  await renderDetail();

  await user.click(screen.getByRole("button", { name: "Delete" }));
  expect(
    await screen.findByRole("heading", { name: "Delete this purchase?" }),
  ).toBeInTheDocument();
});
