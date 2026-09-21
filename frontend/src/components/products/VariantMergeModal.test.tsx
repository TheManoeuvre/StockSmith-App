import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
}));

const { setRoutes, calls, FakeApiError } = await import("../../test/fakeBackend");
const { VariantMergeModal } = await import("./VariantMergeModal");

const variant = (id: number, name: string) =>
  ({ id, product_id: 7, variant_name: name, sku_suffix: null, is_active: true, current_stock: 0 }) as never;

const LOSER = variant(11, "4 Stud Standard");
const SURVIVOR = variant(10, "4 Stud");
const OTHER = variant(12, "6 Stud");

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

let plan: Record<string, unknown>;
let mergeResponses: (() => unknown)[];

beforeEach(() => {
  plan = {
    loser: unit(11, "4 Stud Standard", 4),
    survivor: unit(10, "4 Stud", 6),
    stock_to_move: 4,
    open_lines: [{ order_id: 3, order_reference: "E-1", qty: 2 }],
    bom_differs: true,
    kitting_differs: false,
    loser_bom: [line("Oak", "12")],
    survivor_bom: [line("Filament", "10")],
    loser_kitting: [],
    survivor_kitting: [],
    live_listings: [],
    blockers: [],
  };
  mergeResponses = [
    () => ({
      survivor: { ...(SURVIVOR as object), current_stock: 10 },
      stock_moved: 4,
      open_lines_moved: 1,
      warnings: [],
    }),
  ];
  setRoutes([
    { method: "POST", path: "/variants/11/merge/preview", respond: () => plan },
    { method: "POST", path: "/variants/11/merge", respond: () => mergeResponses.shift()!() },
  ]);
});

// The stock sentence wraps its number in <strong>, so match the whole list item.
const stockSentence = (text: string) =>
  screen.findByText((_, node) => node?.tagName === "LI" && (node.textContent ?? "").includes(text));

function renderModal(siblings = [SURVIVOR, OTHER]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onMerged = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <VariantMergeModal loser={LOSER} siblings={siblings} onClose={() => undefined} onMerged={onMerged} />
    </QueryClientProvider>
  );
  return onMerged;
}

it("previews once a survivor is chosen and offers the BOM choice only where they differ", async () => {
  const user = userEvent.setup();
  renderModal();

  await user.selectOptions(screen.getByRole("combobox"), "10");

  expect(await stockSentence("4 in stock will move")).toBeInTheDocument();
  expect(screen.getByText(/2 units on 1 open order/)).toBeInTheDocument();
  expect(screen.getByText(/Bill of materials differs/)).toBeInTheDocument();
  expect(screen.queryByText(/Kitting BOM differs/)).not.toBeInTheDocument();
  expect(screen.getByText(/Oak × 12/)).toBeInTheDocument();
});

it("sends the chosen BOM side and reports the outcome", async () => {
  const user = userEvent.setup();
  const onMerged = renderModal();

  await user.selectOptions(screen.getByRole("combobox"), "10");
  await screen.findByText(/Bill of materials differs/);
  await user.click(screen.getByRole("radio", { name: /Take "4 Stud Standard"'s/ }));
  await user.click(screen.getByRole("button", { name: "Merge" }));

  await waitFor(() =>
    expect(calls.find((c) => c.path === "/variants/11/merge")?.body).toEqual({
      target_id: 10,
      bom: "take_loser",
      kitting: "keep_survivor",
      on_live_listing: "ask",
    })
  );
  expect(await screen.findByText(/Merged "4 Stud Standard" into "4 Stud"/)).toBeInTheDocument();
  expect(onMerged).toHaveBeenCalled();
});

it("asks before zeroing a live listing, then re-sends with proceed", async () => {
  plan.live_listings = [{ platform: "etsy", published_sku: "BPP-11", external_listing_id: "5" }];
  mergeResponses = [
    () => {
      throw new FakeApiError(409, "live", {
        code: "live_listing_conflicts",
        message: "live",
        conflicts: [{ platform: "etsy", published_sku: "BPP-11", external_listing_id: "5", message: "On Etsy." }],
      });
    },
    () => ({ survivor: SURVIVOR, stock_moved: 4, open_lines_moved: 0, warnings: ["etsy could not be set to 0"] }),
  ];
  const user = userEvent.setup();
  renderModal();

  await user.selectOptions(screen.getByRole("combobox"), "10");
  await stockSentence("4 in stock will move");
  await user.click(screen.getByRole("button", { name: "Merge" }));

  expect(await screen.findByText("Live on a marketplace")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Set to 0 and merge" }));

  await waitFor(() => {
    const merges = calls.filter((c) => c.path === "/variants/11/merge");
    expect(merges.map((c) => (c.body as { on_live_listing: string }).on_live_listing)).toEqual(["ask", "proceed"]);
  });
  expect(await screen.findByText(/etsy could not be set to 0/)).toBeInTheDocument();
});

it("disables Merge while a blocker stands", async () => {
  plan.blockers = ["One of these variants is on an open stock take."];
  const user = userEvent.setup();
  renderModal();

  await user.selectOptions(screen.getByRole("combobox"), "10");

  expect(await screen.findByText(/open stock take/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Merge" })).toBeDisabled();
});

it("preselects the only possible survivor", async () => {
  renderModal([SURVIVOR]);
  expect(await stockSentence("4 in stock will move")).toBeInTheDocument();
});
