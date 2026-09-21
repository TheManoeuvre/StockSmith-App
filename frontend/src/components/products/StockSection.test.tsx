import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const productsApi = {
  get: vi.fn(),
  listVariants: vi.fn(),
  getBom: vi.fn(),
  listStockHistory: vi.fn(),
};
const buildsApi = { create: vi.fn() };
const stockAdjustmentsApi = { create: vi.fn() };
const listMaterials = vi.fn();
const listSubstitutes = vi.fn();

vi.mock("../../api/products", () => ({ productsApi, buildsApi, stockAdjustmentsApi }));
vi.mock("../../api/materials", () => ({ materialsApi: { list: () => listMaterials() } }));
vi.mock("../../api/materialSubstitutes", () => ({
  materialSubstitutesApi: { list: (id: number) => listSubstitutes(id) },
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));
vi.mock("../../hooks/useMaterialCategories", () => ({
  useMaterialCategories: () => ({ categories: [], byName: new Map() }),
}));

const { StockSection } = await import("./StockSection");

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <StockSection productId={1} />
    </QueryClientProvider>,
  );
}

describe("StockSection adjust reason", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    productsApi.get.mockResolvedValue({
      id: 1,
      current_stock: 12,
      allocated_qty: 2,
      push_buildable_capacity: true,
      platform_ceiling_qty: null,
      max_buildable: 5,
      expected_max_buildable: 5,
      max_sellable: 15,
      max_sellable_reason: null,
      expected_max_sellable: 15,
      theoretical_max_sellable: 15,
      theoretical_max_sellable_reason: null,
    });
    productsApi.listVariants.mockResolvedValue([]);
    productsApi.getBom.mockResolvedValue([]);
    productsApi.listStockHistory.mockResolvedValue([]);
    listMaterials.mockResolvedValue([]);
    stockAdjustmentsApi.create.mockResolvedValue({});
  });

  it("offers preset reasons and reveals a detail field for Other…", async () => {
    const user = userEvent.setup();
    renderSection();

    const reason = await screen.findByRole("combobox", { name: "Reason" });
    expect(screen.getByRole("option", { name: "Pick a reason…" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Failed print / scrapped" })).toBeInTheDocument();

    await user.selectOptions(reason, "Other…");
    expect(screen.getByLabelText("Reason detail")).toBeInTheDocument();
  });

  it("submits the chosen preset as the adjustment reason", async () => {
    const user = userEvent.setup();
    renderSection();

    const reason = await screen.findByRole("combobox", { name: "Reason" });
    await user.selectOptions(reason, "Built to stock");
    await user.type(screen.getByRole("spinbutton", { name: /adjust by/i }), "3");

    const save = screen.getByRole("button", { name: "Save" });
    await waitFor(() => expect(save).toBeEnabled());
    await user.click(save);

    await waitFor(() =>
      expect(stockAdjustmentsApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ product_id: 1, mode: "adjust", value: 3, reason: "Built to stock" }),
      ),
    );
  });
});

describe("StockSection build substitution", () => {
  const petg = { id: 10, name: "PETG White", unit: "g", current_qty: "0.0000", category: "filament" };
  const pla = { id: 11, name: "PLA Ivory", unit: "g", current_qty: "3699.0000", category: "filament" };

  beforeEach(() => {
    vi.clearAllMocks();
    productsApi.get.mockResolvedValue({
      id: 1,
      current_stock: 0,
      allocated_qty: 0,
      push_buildable_capacity: true,
      platform_ceiling_qty: null,
      max_buildable: 0,
      expected_max_buildable: 0,
      max_sellable: 0,
      max_sellable_reason: null,
      expected_max_sellable: 0,
      theoretical_max_sellable: 0,
      theoretical_max_sellable_reason: null,
    });
    productsApi.listVariants.mockResolvedValue([]);
    productsApi.getBom.mockResolvedValue([{ id: 1, product_id: 1, material_id: petg.id, qty_required: "28.0000" }]);
    productsApi.listStockHistory.mockResolvedValue([]);
    listMaterials.mockResolvedValue([petg, pla]);
    listSubstitutes.mockResolvedValue([
      { id: 5, material_id: petg.id, substitute_material_id: pla.id, substitute_material_name: pla.name, rank: 1, is_active: true },
    ]);
    buildsApi.create.mockResolvedValue({});
  });

  it("flags the short line, and records with the fallback only after confirmation", async () => {
    const user = userEvent.setup();
    renderSection();

    expect(await screen.findByText("Not enough material for this build")).toBeInTheDocument();
    expect(screen.getByText(/need 28 g, have 0 g/)).toBeInTheDocument();

    const pick = await screen.findByRole("combobox", { name: "Substitute for PETG White" });
    await user.selectOptions(pick, String(pla.id));
    await user.click(screen.getByRole("button", { name: "Record" }));

    // Nothing sent yet — the swap has to be read and confirmed first.
    expect(buildsApi.create).not.toHaveBeenCalled();
    expect(screen.getByText("Build with substitute materials?")).toBeInTheDocument();
    expect(screen.getByText(/in place of PETG White/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Record build" }));
    await waitFor(() =>
      expect(buildsApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ product_id: 1, qty_built: 1, substitutions: { [petg.id]: pla.id } }),
      ),
    );
  });

  it("sends no substitutions when the short line is left as-is", async () => {
    const user = userEvent.setup();
    renderSection();

    await screen.findByRole("combobox", { name: "Substitute for PETG White" });
    await user.click(screen.getByRole("button", { name: "Record" }));

    await waitFor(() => expect(buildsApi.create).toHaveBeenCalledTimes(1));
    expect(buildsApi.create.mock.calls[0][0].substitutions).toBeNull();
    expect(screen.queryByText("Build with substitute materials?")).not.toBeInTheDocument();
  });
});
