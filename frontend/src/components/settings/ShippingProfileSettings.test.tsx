import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { ShippingProfileSettings } = await import("./ShippingProfileSettings");

function profile(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    name: "Small parcel",
    is_archived: false,
    usage_count: 0,
    price: "3.00",
    price_etsy: "3.60",
    price_ebay: null,
    cost_etsy: "2.10",
    cost_ebay: "0",
    cost_manual: "0",
    etsy_shipping_profile_id: 501,
    ebay_fulfillment_policy_id: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

let refreshResult: Record<string, unknown>;
let statuses: Record<string, unknown>[];

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ShippingProfileSettings />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  refreshResult = {
    platform: "etsy",
    refreshed_at: "2026-09-16T10:00:00Z",
    changed: [{ shipping_profile_id: 1, name: "Small parcel", old_price: "3.60", new_price: "3.95" }],
    unchanged_count: 2,
    skipped_calculated: ["Calc"],
    missing_upstream: ["Gone"],
    error: null,
  };
  statuses = [
    {
      platform: "etsy",
      connected: true,
      linked_count: 3,
      shipping_price_refresh_hours: 24,
      last_shipping_price_refresh_at: null,
    },
    { platform: "ebay", connected: false, linked_count: 0, shipping_price_refresh_hours: null, last_shipping_price_refresh_at: null },
  ];
  setRoutes([
    { method: "GET", path: /^\/shipping-profiles(\?.*)?$/, respond: () => [profile()] },
    { method: "GET", path: "/shipping-profiles/refresh-status", respond: () => statuses },
    { method: "GET", path: "/platforms/etsy/status", respond: () => ({ connected: true }) },
    { method: "GET", path: "/platforms/ebay/status", respond: () => ({ connected: false }) },
    { method: "GET", path: "/shipping-profiles/marketplace/etsy", respond: () => [] },
    { method: "POST", path: "/shipping-profiles/refresh-prices/etsy", respond: () => refreshResult },
    { method: "PATCH", path: "/shipping-profiles/refresh-status/etsy", respond: () => ({ ...statuses[0], shipping_price_refresh_hours: 6 }) },
    {
      method: "GET",
      path: "/shipping-profiles/1/price-events",
      respond: () => [
        {
          id: 2,
          shipping_profile_id: 1,
          platform: "etsy",
          old_price: "3.60",
          new_price: "3.95",
          source: "sync",
          changed_at: "2026-09-16T10:00:00Z",
        },
        { id: 1, shipping_profile_id: 1, platform: "etsy", old_price: null, new_price: "3.60", source: "manual_import", changed_at: "2026-09-10T10:00:00Z" },
      ],
    },
  ]);
});

it("offers a refresh only for connected marketplaces and says when it last ran", async () => {
  renderPanel();
  const button = await screen.findByRole("button", { name: "Refresh from Etsy" });
  expect(button).toHaveProperty("disabled", false);
  expect(screen.queryByRole("button", { name: "Refresh from eBay" })).toBeNull();
  expect(screen.getByText(/3 linked · never refreshed/)).toBeTruthy();
});

it("runs the refresh now and reports what moved, what was skipped and what is missing", async () => {
  renderPanel();
  await userEvent.click(await screen.findByRole("button", { name: "Refresh from Etsy" }));

  await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/shipping-profiles/refresh-prices/etsy")).toBe(true));
  expect(await screen.findByText(/2 unchanged/)).toBeTruthy();
  expect(screen.getByText(/Small parcel: £3.60 → £3.95/)).toBeTruthy();
  expect(screen.getByText(/1 calculated \(skipped\)/)).toBeTruthy();
  expect(screen.getByText(/Missing on Etsy .*: Gone/)).toBeTruthy();
});

it("shows a read failure as nothing changed rather than as a crash", async () => {
  refreshResult = { ...refreshResult, changed: [], error: "rate limited", refreshed_at: null };
  renderPanel();
  await userEvent.click(await screen.findByRole("button", { name: "Refresh from Etsy" }));
  expect(await screen.findByText(/couldn't be read — nothing was changed: rate limited/)).toBeTruthy();
});

it("saves a new refresh interval on blur", async () => {
  renderPanel();
  const input = await screen.findByLabelText("Etsy refresh interval (hours)");
  await userEvent.clear(input);
  await userEvent.type(input, "6");
  await userEvent.tab();
  await waitFor(() => {
    const patch = calls.find((c) => c.method === "PATCH" && c.path === "/shipping-profiles/refresh-status/etsy");
    expect(patch?.body).toEqual({ shipping_price_refresh_hours: 6 });
  });
});

it("shows a profile's price history with the source of each change", async () => {
  renderPanel();
  await userEvent.click(await screen.findByText("Small parcel"));
  await userEvent.click(await screen.findByText(/Price history/));

  expect(await screen.findByText(/£3.60 → £3.95/)).toBeTruthy();
  expect(screen.getByText(/\(scheduled refresh\)/)).toBeTruthy();
  expect(screen.getByText(/unset → £3.60/)).toBeTruthy();
  expect(screen.getByText(/\(pulled by hand\)/)).toBeTruthy();
});
