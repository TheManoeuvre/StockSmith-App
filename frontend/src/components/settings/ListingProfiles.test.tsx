import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { ListingProfiles } = await import("./ListingProfiles");

const ETSY_PROFILE = {
  id: 1,
  platform: "etsy",
  name: "3D printed home",
  is_default: true,
  etsy_taxonomy_id: 1234,
  etsy_who_made: "i_did",
  etsy_when_made: "made_to_order",
  etsy_is_supply: false,
  etsy_shipping_profile_id: 99,
  etsy_return_policy_id: null,
  etsy_shop_section_id: null,
  etsy_processing_min: null,
  etsy_processing_max: null,
  ebay_category_id: null,
  ebay_condition: null,
  ebay_fulfillment_policy_id: null,
  ebay_payment_policy_id: null,
  ebay_return_policy_id: null,
  ebay_merchant_location_key: null,
  ebay_marketplace_id: null,
};

const TAXONOMY = [
  { id: 1234, name: "Desk Storage", path: "Home & Living > Storage & Organisation > Desk Storage", level: 2 },
];
const SHIPPING = [{ id: "99", label: "UK Standard" }];
const RETURNS = [{ id: "7", label: "Returns and exchanges within 30 days" }];

let profiles: unknown[] = [];
let shipping: unknown = SHIPPING;

function renderPanel(platform: "etsy" | "ebay" = "etsy") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ListingProfiles platform={platform} />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  profiles = [ETSY_PROFILE];
  shipping = SHIPPING;
  setRoutes([
    { method: "GET", path: /\/platforms\/etsy\/taxonomy\/\d+$/, respond: () => TAXONOMY[0] },
    { method: "GET", path: /\/platforms\/etsy\/taxonomy\?/, respond: () => TAXONOMY },
    { method: "GET", path: "/platforms/etsy/shipping-profiles", respond: () => shipping },
    { method: "GET", path: "/platforms/etsy/return-policies", respond: () => RETURNS },
    { method: "GET", path: /\/settings\/listing-profiles\/\w+$/, respond: () => profiles },
    { method: "POST", path: /\/settings\/listing-profiles\/\w+$/, respond: () => ETSY_PROFILE },
    { method: "PATCH", path: /\/settings\/listing-profiles\/\w+\/\d+$/, respond: () => ETSY_PROFILE },
    { method: "DELETE", path: /\/settings\/listing-profiles\/\w+\/\d+$/, respond: () => undefined },
  ]);
});

it("says plainly that nothing can be drafted when there are no profiles", async () => {
  profiles = [];
  renderPanel();
  expect(await screen.findByText(/can't be drafted to Etsy until one exists/)).toBeTruthy();
});

it("marks which profile is the default", async () => {
  renderPanel();
  expect(await screen.findByText("3D printed home")).toBeTruthy();
  expect(screen.getByText("Default")).toBeTruthy();
});

it("asks Etsy's questions on Etsy and eBay's on eBay", async () => {
  // The two marketplaces need genuinely different metadata — a shared form would ask for
  // fields the platform has no use for and omit the ones it refuses the call without.
  renderPanel("etsy");
  await userEvent.click(await screen.findByText("New profile"));
  expect(screen.getByPlaceholderText(/Search Etsy categories/)).toBeTruthy();
  expect(screen.getByRole("combobox", { name: /Who made it/ })).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: /Postage policy id/ })).toBeNull();
});

it("asks for eBay's policies on eBay", async () => {
  profiles = [];
  renderPanel("ebay");
  await userEvent.click(await screen.findByText("New profile"));
  expect(screen.getByRole("textbox", { name: /Postage policy id/ })).toBeTruthy();
  expect(screen.getByRole("textbox", { name: /Location key/ })).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: /Who made it/ })).toBeNull();
});

it("shows option values as readable text, not the marketplace's codes", async () => {
  // A form offering "i_did" and "made_to_order" is asking the user to read an API.
  renderPanel("etsy");
  await userEvent.click(await screen.findByText("New profile"));

  const whoMade = screen.getByRole("combobox", { name: /Who made it/ }) as HTMLSelectElement;
  const labels = [...whoMade.options].map((o) => o.textContent);
  expect(labels).toContain("I did");
  expect(labels).not.toContain("i_did");
  // The value sent to Etsy is still Etsy's own vocabulary — only the label is ours.
  expect([...whoMade.options].map((o) => o.value)).toContain("i_did");
});

it("picks a category by name and stores its id", async () => {
  renderPanel("etsy");
  await userEvent.click(await screen.findByText("New profile"));

  await userEvent.type(screen.getByPlaceholderText(/Search Etsy categories/), "desk");
  // The path is shown because leaf names repeat across Etsy's tree.
  const option = await screen.findByText("Home & Living > Storage & Organisation > Desk Storage");
  await userEvent.click(option);

  await userEvent.type(screen.getByRole("textbox", { name: /Profile name/ }), "Handmade");
  await userEvent.click(screen.getByText("Save"));

  await waitFor(() => {
    const post = calls.find((c) => c.method === "POST");
    expect(post).toBeTruthy();
    expect((post!.body as Record<string, unknown>).etsy_taxonomy_id).toBe(1234);
  });
});

it("offers shipping profiles and return policies by name", async () => {
  renderPanel("etsy");
  await userEvent.click(await screen.findByText("New profile"));

  const shippingSelect = (await screen.findByRole("combobox", {
    name: /Shipping profile/,
  })) as HTMLSelectElement;
  expect([...shippingSelect.options].map((o) => o.textContent)).toContain("UK Standard");

  // Etsy's return policy object carries no name at all, so the label describes what it does.
  const returns = screen.getByRole("combobox", { name: /Return policy/ }) as HTMLSelectElement;
  expect([...returns.options].map((o) => o.textContent)).toContain(
    "Returns and exchanges within 30 days"
  );
});

it("distinguishes 'you have none' from 'we could not ask'", async () => {
  // The two look identical in the data and need completely different actions: one means set
  // one up on Etsy, the other means reconnect to grant a permission.
  shipping = [];
  renderPanel("etsy");
  await userEvent.click(await screen.findByText("New profile"));
  expect(await screen.findByText(/No shipping profiles on this Etsy shop/)).toBeTruthy();
});

it("offers eBay conditions as a list rather than free text", async () => {
  profiles = [];
  renderPanel("ebay");
  await userEvent.click(await screen.findByText("New profile"));

  const condition = screen.getByRole("combobox", { name: /Condition/ }) as HTMLSelectElement;
  const labels = [...condition.options].map((o) => o.textContent);
  expect(labels).toContain("New");
  // "Seconds" is not an eBay value — it maps onto NEW_WITH_DEFECTS, which is what it means.
  expect(labels).toContain("Seconds (new, with a flaw)");
  expect([...condition.options].map((o) => o.value)).toContain("NEW_WITH_DEFECTS");
});

it("won't save a profile with no name", async () => {
  renderPanel();
  await userEvent.click(await screen.findByText("New profile"));
  expect(screen.getByText("Save").closest("button")).toHaveProperty("disabled", true);

  await userEvent.type(screen.getByRole("textbox", { name: /Profile name/ }), "Handmade");
  expect(screen.getByText("Save").closest("button")).toHaveProperty("disabled", false);
});

it("sends the fields it was given and leaves the rest null", async () => {
  renderPanel();
  await userEvent.click(await screen.findByText("New profile"));

  await userEvent.type(screen.getByRole("textbox", { name: /Profile name/ }), "Handmade");
  await userEvent.selectOptions(screen.getByRole("combobox", { name: /Who made it/ }), "i_did");
  await userEvent.click(screen.getByText("Save"));

  await waitFor(() => {
    const post = calls.find((c) => c.method === "POST");
    expect(post).toBeTruthy();
    const body = post!.body as Record<string, unknown>;
    expect(body.name).toBe("Handmade");
    expect(body.etsy_who_made).toBe("i_did");
    // Untouched fields go as null rather than "" — an empty string would be stored as a
    // real value and would then satisfy a required-field check it shouldn't.
    expect(body.etsy_when_made).toBeNull();
  });
});

it("confirms before deleting, and says what happens to the products using it", async () => {
  renderPanel();
  await userEvent.click(await screen.findByText("Delete"));

  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText(/fall back to the default profile/)).toBeTruthy();
  // Nothing is sent until the dialog is confirmed.
  expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);

  await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(1));
});

it("cancelling the delete dialog sends nothing", async () => {
  renderPanel();
  await userEvent.click(await screen.findByText("Delete"));
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
});
