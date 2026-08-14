import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { EtsyBackfillPanel } = await import("./EtsyBackfillPanel");

const PROPOSAL = {
  product_id: 37,
  product_name: "Brick Pencil Pot",
  external_listing_id: "900001",
  listing_title: "Brick Pencil Pot",
  description: "A 3D printed pot.",
  description_chars: 17,
  sale_price: null,
  image_url: "https://i.etsystatic.com/a.jpg",
  variant_prices: [
    { variant_id: 1, variant_name: "Blue", sku: "SKU-0037-BLUE", proposed_price: "12.50" },
  ],
};

let preview: unknown;

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <EtsyBackfillPanel />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  preview = { products: [PROPOSAL], already_complete: 4, unmatched: 2 };
  setRoutes([
    { method: "GET", path: "/platforms/etsy/backfill-preview", respond: () => preview },
    {
      method: "POST",
      path: "/platforms/etsy/backfill",
      respond: () => ({
        products_updated: 1,
        descriptions_filled: 1,
        prices_filled: 1,
        images_filled: 1,
        errors: [],
      }),
    },
  ]);
});

it("does not call Etsy until asked", async () => {
  // A shop-wide crawl on every settings page view is not free, unlike the local-only
  // compatibility panel next door.
  renderPanel();
  await waitFor(() => expect(screen.getByText("Check Etsy")).toBeTruthy());
  expect(calls.filter((c) => c.path.includes("backfill"))).toHaveLength(0);
});

it("shows what can be filled, and accounts for the products it skipped", async () => {
  renderPanel();
  await userEvent.click(screen.getByText("Check Etsy"));

  expect(await screen.findByText(/1 product\(s\) have something to fill in/)).toBeTruthy();
  expect(screen.getByText(/4 already complete/)).toBeTruthy();
  expect(screen.getByText(/2 not linked to an Etsy listing/)).toBeTruthy();
  expect(screen.getByText("Brick Pencil Pot")).toBeTruthy();
});

it("pre-ticks everything on offer and sends only the ticked fields", async () => {
  renderPanel();
  await userEvent.click(screen.getByText("Check Etsy"));
  await screen.findByText("Brick Pencil Pot");

  await userEvent.click(screen.getByLabelText(/Hero image/));
  await userEvent.click(screen.getByText(/Fill 1 product\(s\)/));

  await waitFor(() => {
    const post = calls.find((c) => c.method === "POST" && c.path === "/platforms/etsy/backfill");
    expect(post).toBeTruthy();
    const items = (post!.body as { items: { product_id: number; fields: string[] }[] }).items;
    expect(items).toHaveLength(1);
    expect(items[0].product_id).toBe(37);
    expect([...items[0].fields].sort()).toEqual(["description", "price"]);
  });
});

it("reports a clean result without listing every untouched product", async () => {
  preview = { products: [], already_complete: 26, unmatched: 0 };
  renderPanel();
  await userEvent.click(screen.getByText("Check Etsy"));

  expect(await screen.findByText(/Nothing to fill in/)).toBeTruthy();
  expect(screen.getByText(/26 matched product\(s\) are already complete/)).toBeTruthy();
});
