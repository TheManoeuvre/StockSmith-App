import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls, FakeApiError } = await import("../../test/fakeBackend");
const { AttributeValueMergeModal } = await import("./AttributeValueMergeModal");

const unit = (id: number, name: string, stock: number) => ({
  id,
  variant_name: name,
  full_sku: `BPP-${id}`,
  is_active: true,
  current_stock: stock,
  allocated_qty: 0,
});
const line = (material_name: string, qty: string) => ({
  material_id: 1,
  material_name,
  qty_required: qty,
  replaces_material_id: null,
  replaces_material_name: null,
});
const pair = (loserId: number, colour: string, bomDiffers: boolean) => ({
  loser: unit(loserId, `4 Stud Standard / ${colour}`, 3),
  survivor: unit(loserId + 10, `4 Stud / ${colour}`, 5),
  stock_to_move: 3,
  open_lines: [],
  bom_differs: bomDiffers,
  kitting_differs: false,
  loser_bom: [line("Oak", "12")],
  survivor_bom: [line("Filament", "10")],
  loser_kitting: [],
  survivor_kitting: [],
  live_listings: [],
  blockers: [],
});

let plan: Record<string, unknown>;
let mergeResponses: (() => unknown)[];

beforeEach(() => {
  plan = {
    pairs: [pair(1, "Red", true), pair(2, "Blue", false)],
    relabel_only: [{ variant_id: 3, variant_name: "4 Stud Standard / Green" }],
    bom_differs: true,
    kitting_differs: false,
    blockers: [],
  };
  mergeResponses = [
    () => ({ pairs_merged: 2, relabelled: 1, stock_moved: 6, open_lines_moved: 0, warnings: [] }),
  ];
  setRoutes([
    { method: "POST", path: "/products/7/attribute-values/merge/preview", respond: () => plan },
    { method: "POST", path: "/products/7/attribute-values/merge", respond: () => mergeResponses.shift()!() },
  ]);
});

function renderModal(initialSurvivor?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AttributeValueMergeModal
        productId={7}
        slot={1}
        attributeName="Size"
        loserValue="4 Stud Standard"
        candidates={["4 Stud", "6 Stud"]}
        initialSurvivor={initialSurvivor}
        onClose={() => undefined}
      />
    </QueryClientProvider>
  );
}

it("lists pairs and relabels, and shows the BOM choice only for differing pairs", async () => {
  const user = userEvent.setup();
  renderModal();

  await user.selectOptions(screen.getByRole("combobox"), "4 Stud");

  expect(await screen.findByRole("cell", { name: "4 Stud Standard / Red" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "4 Stud Standard / Blue" })).toBeInTheDocument();
  expect(screen.getByText(/just relabelled: 4 Stud Standard \/ Green/)).toBeInTheDocument();
  expect(screen.getByText(/Bill of materials differs on 1 pair/)).toBeInTheDocument();
  // The side-by-side is shown for the differing pair only.
  expect(screen.getAllByText("Oak × 12")).toHaveLength(1);
});

it("sends one answer for every pair and reports the totals", async () => {
  const user = userEvent.setup();
  renderModal("4 Stud");

  await screen.findByText(/Bill of materials differs/);
  await user.click(screen.getByRole("radio", { name: "Take the merged variant's" }));
  await user.click(screen.getByRole("button", { name: "Merge" }));

  await waitFor(() =>
    expect(calls.find((c) => c.path === "/products/7/attribute-values/merge")?.body).toEqual({
      slot: 1,
      loser_value: "4 Stud Standard",
      survivor_value: "4 Stud",
      bom: "take_loser",
      kitting: "keep_survivor",
      on_live_listing: "ask",
    })
  );
  expect(await screen.findByText(/2 variants merged, 1 relabelled, 6 in stock/)).toBeInTheDocument();
});

it("confirms live listings once for all pairs", async () => {
  mergeResponses = [
    () => {
      throw new FakeApiError(409, "live", {
        code: "live_listing_conflicts",
        message: "live",
        conflicts: [
          { platform: "etsy", published_sku: null, external_listing_id: "1", message: "Red is live on Etsy." },
          { platform: "ebay", published_sku: null, external_listing_id: "2", message: "Blue is live on eBay." },
        ],
      });
    },
    () => ({ pairs_merged: 2, relabelled: 1, stock_moved: 6, open_lines_moved: 0, warnings: [] }),
  ];
  const user = userEvent.setup();
  renderModal("4 Stud");

  await screen.findByRole("cell", { name: "4 Stud Standard / Red" });
  await user.click(screen.getByRole("button", { name: "Merge" }));

  expect(await screen.findByText("Red is live on Etsy.")).toBeInTheDocument();
  expect(screen.getByText("Blue is live on eBay.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Set to 0 and merge" }));

  await waitFor(() =>
    expect(calls.filter((c) => c.path === "/products/7/attribute-values/merge")).toHaveLength(2)
  );
  expect(await screen.findByText(/2 variants merged/)).toBeInTheDocument();
});
