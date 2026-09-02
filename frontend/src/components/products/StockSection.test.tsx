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

vi.mock("../../api/products", () => ({ productsApi, buildsApi, stockAdjustmentsApi }));
vi.mock("../../api/materials", () => ({ materialsApi: { list: () => listMaterials() } }));
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
