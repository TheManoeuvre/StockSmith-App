import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { EtsyProfileProposalsPanel } = await import("./EtsyProfileProposalsPanel");

function proposal(overrides: Record<string, unknown> = {}) {
  return {
    index: 0,
    suggested_name: "Handmade (category 1234)",
    is_complete: true,
    product_count: 18,
    product_names: ["Brick Pencil Pot", "Bike Keychain"],
    taxonomy_id: 1234,
    who_made: "i_did",
    when_made: "made_to_order",
    is_supply: false,
    return_policy_id: 7,
    processing_min: 1,
    processing_max: 3,
    ...overrides,
  };
}

let proposals: unknown[];
let shippingProposals: unknown[];

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <EtsyProfileProposalsPanel />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  proposals = [proposal()];
  shippingProposals = [];
  setRoutes([
    {
      method: "GET",
      path: "/platforms/etsy/profile-proposals",
      respond: () => ({ proposals, shipping_profiles: shippingProposals }),
    },
    {
      method: "GET",
      path: "/shipping-profiles",
      respond: () => [
        { id: 4, name: "Small parcel", etsy_shipping_profile_id: null },
        { id: 5, name: "Already linked", etsy_shipping_profile_id: 123 },
      ],
    },
    {
      method: "POST",
      path: "/platforms/etsy/profile-proposals/apply",
      respond: () => ({
        profiles_created: 1,
        products_assigned: 18,
        shipping_profiles_created: 0,
        shipping_profiles_linked: 0,
        shipping_products_assigned: 0,
      }),
    },
  ]);
});

it("does not crawl the shop until asked", async () => {
  renderPanel();
  await waitFor(() => expect(screen.getByText("Suggest profiles")).toBeTruthy());
  expect(calls).toHaveLength(0);
});

it("shows how many products each suggestion would cover", async () => {
  renderPanel();
  await userEvent.click(screen.getByText("Suggest profiles"));

  expect(await screen.findByText("18 product(s)")).toBeTruthy();
  expect(screen.getByText(/Brick Pencil Pot, Bike Keychain/)).toBeTruthy();
  expect(screen.getByText(/Category 1234/)).toBeTruthy();
});

it("pre-accepts a complete suggestion", async () => {
  renderPanel();
  await userEvent.click(screen.getByText("Suggest profiles"));

  await screen.findByText("18 product(s)");
  expect(screen.getByRole("checkbox")).toHaveProperty("checked", true);
  expect(screen.getByText(/Create 1 profile\(s\)/)).toBeTruthy();
});

it("shows an incomplete suggestion but leaves it unticked", async () => {
  // Still worth showing — seeing that eleven products share an incomplete combination is
  // how you learn which field to go and set. Creating it would produce a profile that
  // can't draft anything.
  proposals = [proposal({ is_complete: false, taxonomy_id: null })];
  renderPanel();
  await userEvent.click(screen.getByText("Suggest profiles"));

  expect(await screen.findByText(/Missing something Etsy requires/)).toBeTruthy();
  expect(screen.getByRole("checkbox")).toHaveProperty("checked", false);
  expect(screen.getByText(/Create 0 profile\(s\)/).closest("button")).toHaveProperty("disabled", true);
});

it("lets a suggestion be renamed before it is accepted", async () => {
  renderPanel();
  await userEvent.click(screen.getByText("Suggest profiles"));

  const name = await screen.findByDisplayValue("Handmade (category 1234)");
  await userEvent.clear(name);
  await userEvent.type(name, "3D printed home");
  await userEvent.click(screen.getByText(/Create 1 profile\(s\)/));

  await waitFor(() => {
    const post = calls.find((c) => c.method === "POST");
    expect(post).toBeTruthy();
    const body = post!.body as { items: { index: number; name: string }[] };
    expect(body.items).toEqual([{ index: 0, name: "3D printed home" }]);
  });
});

it("sends only the suggestions that are still ticked", async () => {
  proposals = [proposal(), proposal({ index: 1, suggested_name: "Vintage", taxonomy_id: 5678 })];
  renderPanel();
  await userEvent.click(screen.getByText("Suggest profiles"));

  await screen.findByDisplayValue("Vintage");
  const [, second] = screen.getAllByRole("checkbox");
  await userEvent.click(second);
  await userEvent.click(screen.getByText(/Create 1 profile\(s\)/));

  await waitFor(() => {
    const post = calls.find((c) => c.method === "POST");
    const body = post!.body as { items: { index: number }[] };
    expect(body.items.map((i) => i.index)).toEqual([0]);
  });
});

it("reports what it created", async () => {
  renderPanel();
  await userEvent.click(screen.getByText("Suggest profiles"));
  await screen.findByText("18 product(s)");
  await userEvent.click(screen.getByText(/Create 1 profile\(s\)/));

  expect(await screen.findByText(/Created/)).toBeTruthy();
  expect(await screen.findByText("18")).toBeTruthy();
});

it("says so when there is nothing to suggest", async () => {
  proposals = [];
  renderPanel();
  await userEvent.click(screen.getByText("Suggest profiles"));
  expect(await screen.findByText(/Nothing to suggest/)).toBeTruthy();
});

// --- Shipping profiles: a separate group, because the draft takes its Etsy shipping
// profile from the product's own shipping profile rather than from the listing profile.

function shippingProposal(overrides: Record<string, unknown> = {}) {
  return {
    etsy_shipping_profile_id: 555,
    title: "UK Standard",
    domestic_price: "3.50",
    is_calculated: false,
    product_count: 12,
    product_names: ["Brick Pencil Pot"],
    ...overrides,
  };
}

it("offers each unlinked Etsy shipping profile as its own suggestion, named from Etsy", async () => {
  shippingProposals = [shippingProposal()];
  renderPanel();
  await userEvent.click(screen.getByText("Suggest profiles"));

  expect(await screen.findByDisplayValue("UK Standard")).toBeTruthy();
  expect(screen.getByText(/buyer pays 3.50/)).toBeTruthy();
  expect(screen.getByText(/Create 2 profile\(s\)/)).toBeTruthy();
});

it("creates a new local profile for an accepted shipping suggestion", async () => {
  shippingProposals = [shippingProposal()];
  renderPanel();
  await userEvent.click(screen.getByText("Suggest profiles"));
  const name = await screen.findByDisplayValue("UK Standard");
  await userEvent.clear(name);
  await userEvent.type(name, "Royal Mail 2nd");
  await userEvent.click(screen.getByText(/Create 2 profile\(s\)/));

  await waitFor(() => {
    const post = calls.find((c) => c.method === "POST");
    const body = post!.body as { shipping_items: unknown[] };
    expect(body.shipping_items).toEqual([{ etsy_shipping_profile_id: 555, name: "Royal Mail 2nd" }]);
  });
});

it("can link an existing unlinked local profile instead of creating one", async () => {
  shippingProposals = [shippingProposal()];
  renderPanel();
  await userEvent.click(screen.getByText("Suggest profiles"));
  await screen.findByDisplayValue("UK Standard");

  const picker = (await screen.findByRole("combobox", { name: /Local profile for UK Standard/ })) as HTMLSelectElement;
  // Only profiles not already linked to Etsy are offered.
  await waitFor(() => expect([...picker.options].map((o) => o.textContent)).toContain("Link: Small parcel"));
  expect([...picker.options].map((o) => o.textContent)).not.toContain("Link: Already linked");
  await userEvent.selectOptions(picker, "4");
  await userEvent.click(screen.getByText(/Create 2 profile\(s\)/));

  await waitFor(() => {
    const post = calls.find((c) => c.method === "POST");
    const body = post!.body as { shipping_items: unknown[] };
    expect(body.shipping_items).toEqual([{ etsy_shipping_profile_id: 555, link_shipping_profile_id: 4 }]);
  });
});

it("says a calculated Etsy profile has no fixed price to bring across", async () => {
  shippingProposals = [shippingProposal({ is_calculated: true, domestic_price: null })];
  renderPanel();
  await userEvent.click(screen.getByText("Suggest profiles"));
  expect(await screen.findByText(/calculated on Etsy/)).toBeTruthy();
});
