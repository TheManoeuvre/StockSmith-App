import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { EtsyListingPickerModal } = await import("./EtsyListingPickerModal");

const MAPPING_PATH = "/platforms/etsy/products/37/listings/1234/variation-mapping";

const listing = {
  external_listing_id: "1234",
  title: "Brick Pencil Pot",
  state: "active",
  products: [
    { index: 0, sku: null, variation: "Colour: White", quantity: 3, attributes: { Colour: "White" } },
    { index: 1, sku: "OLD", variation: "Colour: Black", quantity: 2, attributes: { Colour: "Black" } },
  ],
};

function entry(variantId: number, name: string, matchedIndex: number | null, confidence = "exact") {
  return {
    variant_id: variantId,
    variant_name: name,
    stockssmith_attributes: { Colour: name },
    matched_index: matchedIndex,
    matched_variation: matchedIndex === null ? null : listing.products[matchedIndex].variation,
    matched_attributes: matchedIndex === null ? null : listing.products[matchedIndex].attributes,
    match_confidence: confidence,
  };
}

const autoProposal = {
  attribute_pairs: [{ stocksmith_name: "Colour", platform_name: "Colour", source: "exact" }],
  platform_attribute_names: ["Colour", "Finish"],
  entries: [entry(10, "Black", 1), entry(11, "White", 0)],
};

const manualProposal = {
  attribute_pairs: [{ stocksmith_name: "Colour", platform_name: "Finish", source: "manual" }],
  platform_attribute_names: ["Colour", "Finish"],
  entries: [entry(10, "Black", 0, "count_only"), entry(11, "White", 1, "count_only")],
};

function renderModal() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <EtsyListingPickerModal productId={37} onClose={() => {}} />
    </QueryClientProvider>
  );
}

async function selectListing() {
  renderModal();
  await userEvent.click(await screen.findByRole("button", { name: /Brick Pencil Pot/ }));
  await screen.findByText("Map StockSmith units to Etsy variations");
}

function rowSelect(variantName: string): HTMLSelectElement {
  return screen.getByRole("combobox", { name: `Etsy variation for ${variantName}` }) as HTMLSelectElement;
}

beforeEach(() => {
  setRoutes([
    { method: "GET", path: "/platforms/etsy/unadopted-listings", respond: () => ({ total_count: 1, listings: [listing] }) },
    { method: "GET", path: MAPPING_PATH, respond: () => autoProposal },
    { method: "GET", path: new RegExp(`^${MAPPING_PATH}\\?attribute_map=`), respond: () => manualProposal },
    {
      method: "POST",
      path: "/platforms/etsy/products/37/adopt-listing",
      respond: () => ({ summary: {}, units: [{ variant_id: 10 }, { variant_id: 11 }], skus_aligned: false }),
    },
  ]);
});

it("pre-fills each row from the proposal so an exact match needs no clicks", async () => {
  await selectListing();

  expect(rowSelect("Black").value).toBe("1");
  expect(rowSelect("White").value).toBe("0");
  expect(screen.getByRole("button", { name: "Write SKUs & link" })).toHaveProperty("disabled", false);
});

it("shows how each attribute was paired and lets the user re-pair it", async () => {
  await selectListing();

  const pairing = screen.getByRole("combobox", { name: "Etsy attribute for Colour" }) as HTMLSelectElement;
  expect(pairing.value).toBe("Colour");

  // Make a manual row pick first, to prove a re-pair discards it.
  await userEvent.selectOptions(rowSelect("Black"), "0");
  expect(rowSelect("Black").value).toBe("0");

  await userEvent.selectOptions(pairing, "Finish");

  await waitFor(() => expect(screen.getAllByText("(check this)")).toHaveLength(2));
  const repair = calls.find((c) => c.method === "GET" && c.path.includes("attribute_map="));
  expect(repair).toBeTruthy();
  expect(decodeURIComponent(repair!.path.split("attribute_map=")[1])).toBe(JSON.stringify({ Colour: "Finish" }));
  // Rows now reflect the new proposal, not the stale manual pick.
  expect(rowSelect("Black").value).toBe("0");
  expect(rowSelect("White").value).toBe("1");
});

it("sends the effective links — a manual row override wins over the proposal", async () => {
  await selectListing();
  await userEvent.selectOptions(rowSelect("Black"), "0");
  await userEvent.selectOptions(rowSelect("White"), "1");

  await userEvent.click(screen.getByRole("button", { name: "Write SKUs & link" }));

  await screen.findByText(/Linked 2 unit\(s\)/);
  const post = calls.find((c) => c.method === "POST")!;
  expect(post.body).toEqual({
    external_listing_id: "1234",
    listing_title: "Brick Pencil Pot",
    links: [
      { variant_id: 10, product_index: 0 },
      { variant_id: 11, product_index: 1 },
    ],
    write_skus: true,
  });
});

it("keeps the button disabled while an unmatched row is still unpicked", async () => {
  setRoutes([
    { method: "GET", path: "/platforms/etsy/unadopted-listings", respond: () => ({ total_count: 1, listings: [listing] }) },
    {
      method: "GET",
      path: MAPPING_PATH,
      respond: () => ({ ...autoProposal, entries: [entry(10, "Black", 1), entry(11, "White", null, "unmatched")] }),
    },
  ]);
  await selectListing();

  expect(screen.getByText("(pick one)")).toBeTruthy();
  const button = screen.getByRole("button", { name: "Write SKUs & link" });
  expect(button).toHaveProperty("disabled", true);

  await userEvent.selectOptions(rowSelect("White"), "0");
  expect(button).toHaveProperty("disabled", false);
});
