import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { PlatformSyncSection } = await import("./PlatformSyncSection");

function unit(overrides: Record<string, unknown> = {}) {
  return {
    listing_id: 10,
    role: null,
    variant_id: null,
    variant_name: null,
    sku: "SKU-1",
    status: "synced",
    external_listing_id: "A",
    external_title: "Brick Pencil Pot",
    external_variation: null,
    external_state: "active",
    external_quantity: 5,
    last_checked_at: null,
    expected_quantity: 5,
    quantity_mismatch: false,
    additional_listings: [],
    ...overrides,
  };
}

function extra(overrides: Record<string, unknown> = {}) {
  return {
    listing_id: 11,
    external_listing_id: "B",
    role: null,
    status: "synced",
    external_title: "Brick Pencil Pot - free shipping",
    external_variation: null,
    external_state: "active",
    external_quantity: 5,
    last_checked_at: null,
    quantity_mismatch: false,
    ...overrides,
  };
}

let summary: unknown;

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PlatformSyncSection productId={37} platform="etsy" />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  summary = { product_id: 37, product_status: "synced", units: [unit()] };
  setRoutes([
    { method: "GET", path: "/platforms/etsy/products/37/sync-status", respond: () => summary },
    { method: "GET", path: "/platforms/etsy/products/37/draft-readiness", respond: () => ({ can_create: false }) },
    {
      method: "PATCH",
      path: "/platforms/etsy/listings/11",
      respond: () => summary,
    },
  ]);
});

it("shows a unit with a single listing exactly as before", async () => {
  renderSection();

  await screen.findByText("Brick Pencil Pot");
  expect(screen.queryByText(/also listed/)).toBeNull();
  expect(screen.queryByLabelText("Listing label")).toBeNull();
});

it("shows each further listing sharing the SKU under its unit", async () => {
  summary = { product_id: 37, product_status: "synced", units: [unit({ additional_listings: [extra()] })] };
  renderSection();

  await screen.findByText("Brick Pencil Pot - free shipping");
  expect(screen.getByText(/also listed/)).toBeTruthy();
  expect(screen.getAllByLabelText("Listing label")).toHaveLength(2);
});

it("flags a drifted additional listing and offers the correction", async () => {
  summary = {
    product_id: 37,
    product_status: "synced",
    units: [unit({ quantity_mismatch: true, additional_listings: [extra({ external_quantity: 2, quantity_mismatch: true })] })],
  };
  renderSection();

  await screen.findByRole("button", { name: "Push corrections" });
});

it("saves a label on blur, once, and not when nothing changed", async () => {
  summary = { product_id: 37, product_status: "synced", units: [unit({ additional_listings: [extra()] })] };
  const user = userEvent.setup();
  renderSection();
  await screen.findByText("Brick Pencil Pot - free shipping");

  const labels = screen.getAllByLabelText("Listing label");
  await user.click(labels[1]);
  await user.tab(); // untouched: no write
  expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(0);

  await user.click(labels[1]);
  await user.type(labels[1], "Free shipping");
  await user.tab();

  await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1));
  expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ role: "Free shipping" });
});

it("makes an additional listing the primary", async () => {
  summary = { product_id: 37, product_status: "synced", units: [unit({ additional_listings: [extra()] })] };
  const user = userEvent.setup();
  renderSection();
  const row = (await screen.findByText("Brick Pencil Pot - free shipping")).closest("tr") as HTMLElement;

  await user.click(within(row).getByRole("button", { name: "Make primary" }));

  await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ is_primary: true }));
});
