import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { ProductPlatformSettingsPanel } = await import("./ProductPlatformSettingsPanel");

const SETTINGS = {
  product_id: 37,
  platform: "etsy",
  listing_profile_id: null,
  is_target: null,
  listing_title: null,
  listing_description: null,
  resolved_title: "Brick Pencil Pot",
  resolved_title_source: "product_name",
  resolved_description: "A 3D printed desk tidy.",
  resolved_description_source: "product_description",
};

const PROFILES = [
  {
    id: 1,
    platform: "etsy",
    name: "3D printed home",
    etsy_taxonomy_id: 1234,
    etsy_who_made: "i_did",
    etsy_when_made: "made_to_order",
    etsy_is_supply: false,
    etsy_shipping_profile_id: null,
    etsy_readiness_state_id: 7,
    etsy_return_policy_id: 55,
    etsy_shop_section_id: null,
    etsy_processing_min: null,
    etsy_processing_max: null,
  },
  {
    id: 2,
    platform: "etsy",
    name: "Vintage",
    etsy_taxonomy_id: null,
    etsy_who_made: "someone_else",
    etsy_when_made: "1990s",
    etsy_is_supply: null,
    etsy_shipping_profile_id: null,
    etsy_readiness_state_id: null,
    etsy_return_policy_id: null,
    etsy_shop_section_id: null,
    etsy_processing_min: null,
    etsy_processing_max: null,
  },
];

const LOOKUPS = [
  {
    method: "GET" as const,
    path: "/platforms/etsy/taxonomy/1234",
    respond: () => ({ id: 1234, name: "Pencil Holders", path: "Home & Living › Office › Pencil Holders", level: 3 }),
  },
  {
    method: "GET" as const,
    path: "/platforms/etsy/readiness-states",
    respond: () => [{ id: "7", label: "Made to order (1-3 days)" }],
  },
  {
    method: "GET" as const,
    path: "/platforms/etsy/return-policies",
    respond: () => [{ id: "55", label: "Returns within 30 days" }],
  },
];

const LIMITS = [
  { field: "title_max_length", label: "Title length", kind: "int", default_value: "140", override_value: null, effective_value: "140", is_override: false, note: null },
];

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

let currentReadiness: unknown;
let currentSettings: unknown;

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ProductPlatformSettingsPanel productId={37} platform="etsy" />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  currentReadiness = readiness();
  currentSettings = SETTINGS;
  setRoutes([
    { method: "GET", path: "/platforms/etsy/products/37/draft-readiness", respond: () => currentReadiness },
    { method: "GET", path: "/platforms/etsy/products/37/settings", respond: () => currentSettings },
    { method: "PUT", path: "/platforms/etsy/products/37/settings", respond: () => currentSettings },
    { method: "GET", path: "/settings/listing-profiles/etsy", respond: () => PROFILES },
    { method: "GET", path: "/settings/platform-limits/etsy", respond: () => LIMITS },
    ...LOOKUPS,
  ]);
});

it("says at a glance whether a draft could be created", async () => {
  renderPanel();
  expect(await screen.findByText("Ready to draft")).toBeTruthy();
});

it("shows blockers without needing to be expanded", async () => {
  // The whole point of a local readiness check is that the user learns what's missing
  // without going looking for it.
  currentReadiness = readiness({
    can_create: false,
    issues: [
      {
        field: "etsy_taxonomy_id",
        severity: "blocker",
        message: "Etsy needs a category (taxonomy) before it will accept a listing.",
        fix_hint: "Set Category on the '3D printed home' profile.",
      },
    ],
  });
  renderPanel();

  expect(await screen.findByText(/1 thing\(s\) missing/)).toBeTruthy();
  expect(screen.getByText(/Etsy needs a category/)).toBeTruthy();
  // Naming where to fix it is what makes the report actionable rather than merely accurate.
  expect(screen.getByText(/Set Category on the '3D printed home' profile\./)).toBeTruthy();
});

it("keeps warnings behind the expander so blockers stay prominent", async () => {
  currentReadiness = readiness({
    issues: [{ field: "images", severity: "warning", message: "No image attached.", fix_hint: null }],
  });
  renderPanel();

  await screen.findByText("Ready to draft");
  expect(screen.queryByText("No image attached.")).toBeNull();

  await userEvent.click(screen.getByText("Show"));
  expect(await screen.findByText("No image attached.")).toBeTruthy();
});

it("says which fallback the title came from rather than passing it off as authored", async () => {
  renderPanel();
  await userEvent.click(await screen.findByText("Show"));
  expect(await screen.findByText(/Using the product name: Brick Pencil Pot/)).toBeTruthy();
});

it("counts title characters against the platform's own limit", async () => {
  renderPanel();
  await userEvent.click(await screen.findByText("Show"));

  const title = await screen.findByRole("textbox", { name: /listing title/i });
  await userEvent.type(title, "Brick Pot");
  expect(await screen.findByText("9 / 140")).toBeTruthy();
});

it("flags a title over the cap while still letting it be typed", async () => {
  // Blocking the keystroke would leave someone unable to paste and then trim.
  setRoutes([
    { method: "GET", path: "/platforms/etsy/products/37/draft-readiness", respond: () => currentReadiness },
    { method: "GET", path: "/platforms/etsy/products/37/settings", respond: () => currentSettings },
    { method: "PUT", path: "/platforms/etsy/products/37/settings", respond: () => currentSettings },
    { method: "GET", path: "/settings/listing-profiles/etsy", respond: () => PROFILES },
    {
      method: "GET",
      path: "/settings/platform-limits/etsy",
      respond: () => [{ ...LIMITS[0], effective_value: "10" }],
    },
  ]);
  renderPanel();
  await userEvent.click(await screen.findByText("Show"));

  const title = await screen.findByRole("textbox", { name: /listing title/i });
  await userEvent.type(title, "Far too long a title");
  const counter = await screen.findByText("20 / 10");
  expect(counter.className).toContain("red");
  expect((title as HTMLInputElement).value).toBe("Far too long a title");
});

it("offers the platform's profiles behind an explicit choice, with no default", async () => {
  renderPanel();
  await userEvent.click(await screen.findByText("Show"));

  const select = await screen.findByRole("combobox", { name: /Listing profile/i });
  const options = [...(select as HTMLSelectElement).options].map((o) => o.textContent);
  expect(options).toEqual(["Choose a profile…", "3D printed home", "Vintage"]);
  expect(screen.getByText("Nothing is applied until a profile is chosen.")).toBeTruthy();
});

it("shows what the chosen profile commits the listing to, by name rather than id", async () => {
  // The name of a profile says nothing about its consequences; the category and
  // processing profile are what the user is actually deciding.
  renderPanel();
  await userEvent.click(await screen.findByText("Show"));
  await userEvent.selectOptions(await screen.findByRole("combobox", { name: /Listing profile/i }), "1");

  expect(await screen.findByText("Home & Living › Office › Pencil Holders")).toBeTruthy();
  expect(await screen.findByText("Made to order (1-3 days)")).toBeTruthy();
  expect(await screen.findByText("Returns within 30 days")).toBeTruthy();
  expect(screen.getByText("I did")).toBeTruthy();
  expect(screen.getByText("Made to order")).toBeTruthy();
  expect(screen.getByText("A finished product")).toBeTruthy();
  expect(screen.queryByText("Nothing is applied until a profile is chosen.")).toBeNull();
});

it("names what a chosen profile is missing instead of leaving a blank", async () => {
  renderPanel();
  await userEvent.click(await screen.findByText("Show"));
  await userEvent.selectOptions(await screen.findByRole("combobox", { name: /Listing profile/i }), "2");

  expect(await screen.findByText("1990s")).toBeTruthy();
  // Category and processing profile are both unset on "Vintage".
  expect(screen.getAllByText("Not set").length).toBeGreaterThanOrEqual(2);
});

it("summarises the saved profile on open without a fresh pick", async () => {
  currentSettings = { ...SETTINGS, listing_profile_id: 1 };
  renderPanel();
  await userEvent.click(await screen.findByText("Show"));
  expect(await screen.findByText("Home & Living › Office › Pencil Holders")).toBeTruthy();
});

it("saves the chosen profile and the listing copy together", async () => {
  renderPanel();
  await userEvent.click(await screen.findByText("Show"));

  await userEvent.selectOptions(await screen.findByRole("combobox", { name: /Listing profile/i }), "2");
  await userEvent.type(await screen.findByRole("textbox", { name: /listing title/i }), "Etsy title");
  await userEvent.click(screen.getByText("Save"));

  await waitFor(() => {
    const put = calls.find((c) => c.method === "PUT");
    expect(put).toBeTruthy();
    const body = put!.body as Record<string, unknown>;
    expect(body.listing_profile_id).toBe(2);
    expect(body.listing_title).toBe("Etsy title");
  });
});
