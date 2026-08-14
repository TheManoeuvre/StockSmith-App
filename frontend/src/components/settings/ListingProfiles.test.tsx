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

let profiles: unknown[] = [];

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
  setRoutes([
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
  expect(screen.getByRole("spinbutton", { name: /Category \(taxonomy id\)/ })).toBeTruthy();
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
  await userEvent.type(screen.getByRole("spinbutton", { name: /Category \(taxonomy id\)/ }), "1234");
  await userEvent.selectOptions(screen.getByRole("combobox", { name: /Who made it/ }), "i_did");
  await userEvent.click(screen.getByText("Save"));

  await waitFor(() => {
    const post = calls.find((c) => c.method === "POST");
    expect(post).toBeTruthy();
    const body = post!.body as Record<string, unknown>;
    expect(body.name).toBe("Handmade");
    expect(body.etsy_taxonomy_id).toBe(1234);
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
