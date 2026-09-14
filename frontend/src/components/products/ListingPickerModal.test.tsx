import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { ListingPickerModal } = await import("./ListingPickerModal");

const MAPPING_PATH = "/platforms/ebay/products/37/listings/227269664481/variation-mapping";

const candidate = {
  external_listing_id: "227269664481",
  title: "Aqara G400 mount",
  listing_type: "FixedPriceItem",
  skus: ["OLD-1", "OLD-2"],
  variation_specifics: [{ Shade: "White" }, { Shade: "Black" }],
  quantity: 4,
  ineligibility_reasons: [],
  detail_loaded: true,
};

function entry(variantId: number, name: string, sku: string | null, confidence = "exact") {
  return {
    variant_id: variantId,
    variant_name: name,
    stockssmith_attributes: { Colour: name },
    matched_sku: sku,
    matched_variation_specifics: null,
    match_confidence: confidence,
  };
}

// Names don't line up ("Colour" vs "Shade") and the tie-free value overlap makes the
// pairing an inference — flagged, but still pre-filled.
const autoProposal = {
  attribute_pairs: [{ stocksmith_name: "Colour", platform_name: "Shade", source: "inferred" }],
  platform_attribute_names: ["Shade"],
  entries: [entry(10, "Black", "OLD-2"), entry(11, "White", "OLD-1")],
};

const unpairedProposal = {
  attribute_pairs: [{ stocksmith_name: "Colour", platform_name: null, source: "unmatched" }],
  platform_attribute_names: ["Shade"],
  entries: [entry(10, "Black", "OLD-1", "count_only"), entry(11, "White", "OLD-2", "count_only")],
};

function rowSelect(variantName: string): HTMLSelectElement {
  return screen.getByRole("combobox", { name: `eBay SKU for ${variantName}` }) as HTMLSelectElement;
}

async function selectListing() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ListingPickerModal productId={37} onClose={() => {}} />
    </QueryClientProvider>
  );
  await userEvent.click(await screen.findByRole("button", { name: /Aqara G400 mount/ }));
  await screen.findByText("Map StockSmith variants to eBay SKUs");
}

beforeEach(() => {
  setRoutes([
    {
      method: "GET",
      path: "/platforms/ebay/products/37/unmigrated-listings",
      respond: () => ({ total_count: 1, eligible_count: 1, listings: [candidate] }),
    },
    { method: "GET", path: MAPPING_PATH, respond: () => autoProposal },
    { method: "GET", path: new RegExp(`^${MAPPING_PATH}\\?attribute_map=`), respond: () => unpairedProposal },
  ]);
});

it("pre-fills rows from the proposal and flags an inferred attribute pairing", async () => {
  await selectListing();

  expect(rowSelect("Black").value).toBe("OLD-2");
  expect(rowSelect("White").value).toBe("OLD-1");
  expect(screen.getByText("(matched by values, check this)")).toBeTruthy();
  expect((screen.getByRole("combobox", { name: "eBay attribute for Colour" }) as HTMLSelectElement).value).toBe(
    "Shade"
  );
});

it("re-pairing an attribute re-requests the proposal and drops manual row picks", async () => {
  await selectListing();
  await userEvent.selectOptions(rowSelect("Black"), "OLD-1");
  expect(rowSelect("Black").value).toBe("OLD-1");

  // Unpair it: "— not on listing —" sends null.
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "eBay attribute for Colour" }), "");

  await waitFor(() => expect(screen.getByText("(pick one)")).toBeTruthy());
  const repair = calls.find((c) => c.method === "GET" && c.path.includes("attribute_map="));
  expect(decodeURIComponent(repair!.path.split("attribute_map=")[1])).toBe(JSON.stringify({ Colour: null }));
  // The positional proposal now shows, not the pick made under the old pairing.
  expect(rowSelect("Black").value).toBe("OLD-1");
  expect(rowSelect("White").value).toBe("OLD-2");
  expect(screen.getAllByText("(check this)")).toHaveLength(2);
});
