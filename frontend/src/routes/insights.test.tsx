import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../api/client", async () => (await import("../test/fakeBackend")).clientMock());
vi.mock("../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  backendHostname: () => Promise.resolve("127.0.0.1"),
}));

const { setRoutes, calls } = await import("../test/fakeBackend");
const { routeTree } = await import("../routeTree.gen");

function totals(over: Record<string, unknown> = {}) {
  return {
    orders: 120,
    units: 140,
    revenue: "1500.00",
    platform_fees: "150.00",
    postage_cost: "180.00",
    materials_cogs: "400.00",
    kitting_cogs: "40.00",
    replacement_cost: "30.00",
    refunded: "45.00",
    failed_build_cost: "55.00",
    net_profit: "600.00",
    margin_percent: "40.0",
    average_order_value: "12.50",
    average_profit_per_order: "5.00",
    refund_rate_percent: "2.9",
    profit_unavailable_orders: 0,
    ...over,
  };
}

function bucket(day: string, revenue: string, profit: string) {
  return { period_start: day, orders: 10, units: 12, revenue, net_profit: profit, margin_percent: "40.0" };
}

function overview(over: Record<string, unknown> = {}) {
  return {
    start: "2026-07-28",
    end: "2026-10-25",
    granularity: "week",
    include_failed_builds: true,
    totals: totals(),
    trend: [bucket("2026-10-12", "700.00", "280.00"), bucket("2026-10-19", "800.00", "320.00")],
    channels: [
      { channel: "etsy", orders: 80, units: 95, revenue: "1000.00", platform_fees: "120.00", net_profit: "400.00", margin_percent: "40.0", average_order_value: "12.50" },
      { channel: "ebay", orders: 40, units: 45, revenue: "500.00", platform_fees: "30.00", net_profit: "200.00", margin_percent: "40.0", average_order_value: "12.50" },
    ],
    compare_start: "2026-04-29",
    compare_end: "2026-07-27",
    compare_totals: totals({ orders: 100, revenue: "1200.00", net_profit: "500.00", margin_percent: "41.7" }),
    compare_trend: [bucket("2026-04-27", "600.00", "250.00"), bucket("2026-05-04", "600.00", "250.00")],
    ...over,
  };
}

function productRow(key: string, name: string, over: Record<string, unknown> = {}) {
  return {
    key,
    name,
    sub: "SKU-1",
    product_id: 1,
    variant_id: null,
    units: 30,
    revenue: "400.00",
    cogs: "120.00",
    failed_build_cost: "10.00",
    net_profit: "180.00",
    net_profit_before_failed_builds: "190.00",
    margin_percent: "45.0",
    margin_before_failed_builds_percent: "47.5",
    profit_per_unit: "6.00",
    share_of_profit_percent: "30.0",
    fail_rate_percent: "10.0",
    channel_units: { etsy: 20, ebay: 10 },
    trend: ["10.00", "20.00", "15.00"],
    ...over,
  };
}

function products(over: Record<string, unknown> = {}) {
  return {
    view: "products",
    rows: [productRow("p1", "Linen apron"), productRow("p2", "Stoneware mug", { product_id: 2, margin_percent: "20.0", net_profit: "60.00", units: 90 })],
    total_rows: 23,
    page: 1,
    page_size: 10,
    unmapped_units: 4,
    profit_unavailable_units: 0,
    totals: totals(),
    ...over,
  };
}

const costs = {
  include_failed_builds: true,
  waterfall: [
    { key: "gross", label: "Gross takings", amount: "1545.00" },
    { key: "refunds", label: "Refunds", amount: "-45.00" },
    { key: "fees", label: "Platform fees", amount: "-150.00" },
    { key: "postage", label: "Postage", amount: "-180.00" },
    { key: "materials", label: "Materials", amount: "-400.00" },
    { key: "packaging", label: "Packaging", amount: "-40.00" },
    { key: "resends", label: "Resends", amount: "-30.00" },
    { key: "failed_builds", label: "Failed builds", amount: "-55.00" },
    { key: "net_profit", label: "Net profit", amount: "600.00" },
  ],
  channels: [
    { channel: "etsy", revenue: "1000.00", fees: "120.00", postage: "120.00", materials: "270.00", packaging: "25.00", resends: "20.00", failed_builds: "35.00", net_profit: "410.00" },
  ],
  materials_by_category: [{ key: "filament", label: "Filament", amount: "300.00" }],
  resends_by_reason: [{ key: "faulty_item", label: "Faulty item", amount: "30.00" }],
  failed_builds: [productRow("p2", "Stoneware mug", { failed_build_cost: "55.00" })],
  failed_builds_total: "55.00",
  failed_builds_percent_of_materials: "12.1",
};

const quality = {
  cogs_pending: 3,
  postage_missing: 2,
  financials_not_synced: 1,
  unmapped_lines: 1,
  flagged_total: 5,
  flagged: [{ order_id: 7, external_order_id: "R-1007", channel: "etsy", placed_on: "2026-10-20", issues: ["cogs_pending"] }],
};

function install(over: { overview?: unknown; products?: unknown } = {}) {
  setRoutes([
    { method: "GET", path: "/system/status", respond: () => ({ status: "ok" }) },
    { method: "GET", path: /^\/insights\/overview\?/, respond: () => over.overview ?? overview() },
    { method: "GET", path: /^\/insights\/products\?/, respond: () => over.products ?? products() },
    { method: "GET", path: /^\/insights\/costs\?/, respond: () => costs },
    { method: "GET", path: /^\/insights\/quality\?/, respond: () => quality },
    { method: "GET", path: /.*/, respond: () => [] },
  ]);
}

async function renderInsights() {
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: ["/insights"] }) });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", { name: "Insights" }, { timeout: 5000 });
  return within(await screen.findByRole("main"));
}

const lastCall = (fragment: string) => [...calls].reverse().find((c) => c.path.includes(fragment));

beforeEach(() => install());

it("shows the headline figures with their change against the previous period", async () => {
  const page = await renderInsights();

  expect(await page.findByText("£1,500.00")).toBeInTheDocument();
  expect(page.getByText("£600.00")).toBeInTheDocument();
  expect(page.getAllByText("40.0%").length).toBeGreaterThan(0);
  // Revenue 1500 against 1200: direction and size are in the text, not only the colour.
  expect(page.getAllByText("▲ 25.0%").length).toBeGreaterThan(0);
  // Margin is a percentage, so its change is in points.
  expect(page.getByText("▼ 1.7 pts")).toBeInTheDocument();
});

it("asks for the 90-day range with a previous-period comparison by default", async () => {
  await renderInsights();
  await waitFor(() => expect(lastCall("/insights/overview")).toBeDefined());

  const path = lastCall("/insights/overview")!.path;
  expect(path).toContain("granularity=week");
  expect(path).toContain("compare_start=");
  expect(path).toContain("include_failed_builds=true");
  expect(path).not.toContain("channels=");
});

it("sends the chosen channels, and none at all when they are all deselected", async () => {
  const user = userEvent.setup();
  const page = await renderInsights();
  await page.findByText("£1,500.00");

  await user.click(page.getByRole("button", { name: "✓ Etsy" }));
  await waitFor(() => expect(lastCall("/insights/overview")!.path).toContain("channels=ebay%2Csquare%2Cmanual"));

  for (const name of ["eBay", "Square", "Manual"]) await user.click(page.getByRole("button", { name: `✓ ${name}` }));
  expect(await page.findByText("No channels selected")).toBeInTheDocument();
});

it("filters the page to one channel when its row is clicked", async () => {
  const user = userEvent.setup();
  const page = await renderInsights();
  await page.findByText("By channel");

  // The chip reads "✓ eBay"; the plain "eBay" button is the one in the channel table.
  await user.click(page.getByRole("button", { name: "eBay" }));
  await waitFor(() => expect(lastCall("/insights/overview")!.path).toContain("channels=ebay"));
});

it("compares This year with the same dates last year and draws the whole year", async () => {
  const user = userEvent.setup();
  const page = await renderInsights();
  await page.findByText("£1,500.00");

  await user.click(page.getByRole("button", { name: "This year" }));

  await waitFor(() => {
    const overviewCalls = calls.filter((c) => c.path.includes("/insights/overview"));
    expect(overviewCalls.some((c) => /start=\d{4}-01-01/.test(c.path) && c.path.includes("compare_start="))).toBe(true);
    // The second request is last year in full, for the comparison line to run on to December.
    expect(overviewCalls.some((c) => /end=\d{4}-12-31/.test(c.path) && !c.path.includes("compare_start="))).toBe(true);
  });
  expect(await page.findByText(/Dashed line is \d{4}, through to December/)).toBeInTheDocument();
});

it("lists products a page at a time and re-sorts on the server", async () => {
  const user = userEvent.setup();
  const page = await renderInsights();
  await user.click(page.getByRole("button", { name: "Products" }));

  expect((await page.findAllByText("Linen apron")).length).toBeGreaterThan(0);
  expect(page.getByText(/Showing 1–10 of 23/)).toBeInTheDocument();
  // 90 units at 20% is a high-volume, low-margin product.
  expect(page.getAllByText("High volume, low margin").length).toBeGreaterThan(1);
  // Never-built products show a dash, a built one its failure rate.
  expect(page.getAllByText("10.0%").length).toBeGreaterThan(0);

  await user.click(page.getByRole("button", { name: /Sort by Units|^Units/ }));
  await waitFor(() => expect(lastCall("/insights/products")!.path).toContain("sort=units"));

  await user.click(page.getByRole("button", { name: "Next ›" }));
  await waitFor(() => expect(lastCall("/insights/products")!.path).toContain("page=2"));
});

it("shows unmapped units on the last page without inventing a profit for them", async () => {
  install({ products: products({ total_rows: 2 }) });
  const user = userEvent.setup();
  const page = await renderInsights();
  await user.click(page.getByRole("button", { name: "Products" }));

  expect(await page.findByText("Unmapped SKUs")).toBeInTheDocument();
  expect(page.getByText("pending")).toBeInTheDocument();
  expect(page.getByText("Total (matches Overview)")).toBeInTheDocument();
});

it("shows where the money goes, including failed builds", async () => {
  const user = userEvent.setup();
  const page = await renderInsights();
  await user.click(page.getByRole("button", { name: "Costs" }));

  expect(await page.findByText("Where the money goes")).toBeInTheDocument();
  expect(page.getByText("Failed builds", { selector: "h2" })).toBeInTheDocument();
  expect(page.getByText(/£55.00 of material went on failed builds \(12.1% of all material used\)/)).toBeInTheDocument();
  expect(page.getByText("Faulty item")).toBeInTheDocument();
  // 10% failure adds 11.1% to the materials cost of each good unit.
  expect(page.getByText("+11.1%")).toBeInTheDocument();
});

it("flags data quality in the chip and lists the orders behind it", async () => {
  const user = userEvent.setup();
  const page = await renderInsights();

  await user.click(await page.findByRole("button", { name: /Data confidence: 5 flagged/ }));
  const dialog = await page.findByRole("dialog", { name: "Data confidence" });
  expect(within(dialog).getByText("COGS pending")).toBeInTheDocument();

  await user.click(within(dialog).getByRole("button", { name: "See details" }));
  expect(await page.findByText("R-1007")).toBeInTheDocument();
  expect(page.getByRole("link", { name: /Open in Orders/ })).toBeInTheDocument();
});

it("explains an empty period instead of showing zeros", async () => {
  install({ overview: overview({ totals: totals({ orders: 0, revenue: "0.00", net_profit: "0.00" }), trend: [], channels: [] }) });
  const page = await renderInsights();

  expect(await page.findByText(/No orders between/)).toBeInTheDocument();
  expect(page.getByRole("button", { name: "Reset to last 90 days" })).toBeInTheDocument();
});

it("marks profit as pending when some orders have not synced their financials", async () => {
  install({ overview: overview({ totals: totals({ profit_unavailable_orders: 4 }) }) });
  const page = await renderInsights();

  expect(await page.findByText("4 orders not yet synced")).toBeInTheDocument();
});
