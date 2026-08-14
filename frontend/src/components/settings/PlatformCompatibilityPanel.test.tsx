import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));
// The panel deep-links to a product; the router itself isn't under test here.
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a href="#">{children}</a>,
}));

const { setRoutes } = await import("../../test/fakeBackend");
const { PlatformCompatibilityPanel } = await import("./PlatformCompatibilityPanel");

function report(overrides: Record<string, unknown> = {}) {
  return {
    platform: "etsy",
    total_products: 26,
    blocked_count: 0,
    warning_count: 0,
    products: [],
    ...overrides,
  };
}

const BLOCKED_PRODUCT = {
  product_id: 37,
  product_name: "Brick Pencil Pot",
  product_sku: "SKU-0037",
  is_blocked: true,
  violations: [],
  units: [
    {
      variant_id: 5,
      variant_name: "6 Stud / Sunflower Yellow",
      sku: "SKU-0037-6-STUD-SUNFLOWER-YELLOW-XL",
      violations: [
        {
          field: "sku_max_length",
          severity: "blocker",
          current_value: "SKU-0037-6-STUD-SUNFLOWER-YELLOW-XL",
          current_length: 35,
          limit: "32",
          imposed_by: "etsy",
          message: "SKU is 35 characters, over the 32 (Etsy's limit) of 32.",
          suggested_value: null,
        },
      ],
    },
  ],
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PlatformCompatibilityPanel platform="etsy" />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  setRoutes([
    { method: "GET", path: "/platforms/etsy/catalogue-compatibility", respond: () => currentReport },
  ]);
});

let currentReport: unknown = report();

it("renders nothing at all when the catalogue is clean", async () => {
  currentReport = report();
  const { container } = renderPanel();
  // A panel that permanently announces "0 problems" trains people to stop reading it,
  // and this one has to be noticed on the day it finally says something.
  await waitFor(() => expect(container).toBeEmptyDOMElement());
});

it("summarises how many products are affected out of the whole catalogue", async () => {
  currentReport = report({ blocked_count: 1, warning_count: 2, products: [BLOCKED_PRODUCT] });
  renderPanel();
  expect(await screen.findByText(/of 26 product\(s\) don't fit Etsy's limits/)).toBeTruthy();
  expect(screen.getByText(/1 blocked/)).toBeTruthy();
});

it("keeps variation-level detail collapsed until asked for", async () => {
  currentReport = report({ blocked_count: 1, products: [BLOCKED_PRODUCT] });
  renderPanel();

  // A 76-variation product would otherwise bury the summary it sits under.
  expect(await screen.findByText("Brick Pencil Pot")).toBeTruthy();
  expect(screen.queryByText(/over the 32/)).toBeNull();

  await userEvent.click(screen.getByText(/Show 1 variation issue\(s\)/));
  expect(screen.getByText(/over the 32/)).toBeTruthy();
  expect(screen.getByText("6 Stud / Sunflower Yellow")).toBeTruthy();
});

it("distinguishes a blocker from something that merely needs adjusting", async () => {
  currentReport = report({
    warning_count: 1,
    products: [
      {
        product_id: 12,
        product_name: "Bike Keychain",
        product_sku: "SKU-0029",
        is_blocked: false,
        violations: [
          {
            field: "title_max_length",
            severity: "warning",
            current_value: "A very long title",
            current_length: 100,
            limit: "80",
            imposed_by: "ebay",
            message: "Title is 100 characters, over the 80 (Ebay's limit) of 80.",
            suggested_value: "A very long titl",
          },
        ],
        units: [],
      },
    ],
  });
  renderPanel();

  expect(await screen.findByText("Needs adjusting")).toBeTruthy();
  expect(screen.queryByText("Blocked")).toBeNull();
  // A suggested fix is shown only where the backend judged it unambiguous.
  expect(screen.getByText("A very long titl")).toBeTruthy();
});
