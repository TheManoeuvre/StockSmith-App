import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { DraftListingModal } = await import("./DraftListingModal");

function readiness(overrides: Record<string, unknown> = {}) {
  return {
    product_id: 37,
    platform: "etsy",
    can_create: true,
    profile_id: 1,
    profile_name: "3D printed home",
    title: "Brick Pencil Pot",
    title_source: "product_name",
    description_chars: 23,
    unit_count: 1,
    priced_unit_count: 1,
    image_count: 1,
    issues: [],
    ...overrides,
  };
}

let current: unknown;
let pushResult: unknown;

function renderModal() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DraftListingModal productId={37} platform="etsy" onClose={() => {}} />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  current = readiness();
  pushResult = {
    external_listing_id: "900001",
    state: "draft",
    units_linked: 1,
    warnings: [],
    publish_blockers: [],
  };
  setRoutes([
    { method: "GET", path: "/platforms/etsy/products/37/draft-readiness", respond: () => current },
    { method: "POST", path: "/platforms/etsy/products/37/draft-listing", respond: () => pushResult },
  ]);
});

it("will not create anything until the consequence is acknowledged", async () => {
  // An Etsy draft can't be deleted from StockSmith. The checkbox is the same gate the
  // irreversible eBay migration uses.
  renderModal();
  const create = await screen.findByRole("button", { name: /Create draft on Etsy/ });
  expect(create).toHaveProperty("disabled", true);

  await userEvent.click(screen.getByRole("checkbox"));
  expect(create).toHaveProperty("disabled", false);
});

it("says plainly that the draft cannot be removed from here", async () => {
  renderModal();
  expect(await screen.findByText(/can't be deleted from StockSmith/)).toBeTruthy();
});

it("shows what would be created before creating it", async () => {
  renderModal();
  expect(await screen.findByText("Brick Pencil Pot")).toBeTruthy();
  expect(screen.getByText("3D printed home")).toBeTruthy();
  expect(screen.getByText("1 of 1 priced")).toBeTruthy();
});

it("refuses to offer creation while something is blocking it", async () => {
  current = readiness({
    can_create: false,
    issues: [
      {
        field: "etsy_taxonomy_id",
        severity: "blocker",
        message: "Etsy needs a category before it will accept a listing.",
        fix_hint: null,
      },
    ],
  });
  renderModal();

  expect(await screen.findByText(/Etsy needs a category/)).toBeTruthy();
  await userEvent.click(screen.getByRole("checkbox"));
  // Acknowledging doesn't override a blocker — the call would simply be refused.
  expect(screen.getByRole("button", { name: /Create draft on Etsy/ })).toHaveProperty("disabled", true);
});

it("sends nothing but the product id", async () => {
  // Everything is re-derived server-side: the preview may be minutes old and this writes
  // to a live shop.
  renderModal();
  await userEvent.click(await screen.findByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: /Create draft on Etsy/ }));

  await waitFor(() => {
    const post = calls.find((c) => c.method === "POST");
    expect(post).toBeTruthy();
    expect(post!.body).toBeUndefined();
  });
});

it("reports what to do next once the draft exists", async () => {
  pushResult = {
    external_listing_id: "900001",
    state: "draft",
    units_linked: 1,
    warnings: [],
    publish_blockers: ["Etsy needs at least one image before this can be published."],
  };
  renderModal();
  await userEvent.click(await screen.findByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: /Create draft on Etsy/ }));

  expect(await screen.findByText(/isn't visible to buyers/)).toBeTruthy();
  expect(screen.getByText(/Before you can publish it/)).toBeTruthy();
  expect(screen.getByText(/needs at least one image/)).toBeTruthy();
});
