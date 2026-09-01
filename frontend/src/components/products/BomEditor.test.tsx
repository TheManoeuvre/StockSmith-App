import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getBom = vi.fn();
const replaceBom = vi.fn();
const listMaterials = vi.fn();

vi.mock("../../api/products", () => ({
  productsApi: {
    getBom: (...args: unknown[]) => getBom(...args),
    replaceBom: (...args: unknown[]) => replaceBom(...args),
  },
}));
vi.mock("../../api/materials", () => ({
  materialsApi: { list: () => listMaterials() },
}));

const { BomEditor } = await import("./BomEditor");

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BomEditor productId={1} />
    </QueryClientProvider>
  );
}

describe("BomEditor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getBom.mockResolvedValue([{ id: 10, product_id: 1, material_id: 1, qty_required: "2" }]);
    replaceBom.mockResolvedValue([{ id: 10, product_id: 1, material_id: 1, qty_required: "4" }]);
    listMaterials.mockResolvedValue([
      { id: 1, name: "Filament", unit: "g", category: "filament", current_qty: "100", allocated_qty: "10", avg_unit_cost: "0.50" },
    ]);
  });

  it("disables Save until something changes, then re-disables after saving", async () => {
    const user = userEvent.setup();
    renderEditor();

    const save = await screen.findByRole("button", { name: /save build bom/i });
    expect(save).toBeDisabled();

    const qty = await screen.findByDisplayValue("2");
    await user.clear(qty);
    await user.type(qty, "4");

    await waitFor(() => expect(save).toBeEnabled());

    await user.click(save);

    await waitFor(() => expect(replaceBom).toHaveBeenCalledWith(1, [{ material_id: 1, qty_required: "4" }]));
    await waitFor(() => expect(save).toBeDisabled());
  });

  it("shows each line's cost and share, and a table total", async () => {
    renderEditor();
    // 2 x £0.50, appearing three times: the row cost, the section-header total, and the
    // table footer total.
    expect(await screen.findAllByText("£1.00")).toHaveLength(3);
    expect(await screen.findByText("100.0%")).toBeInTheDocument();
    expect(await screen.findByText("Total")).toBeInTheDocument();
  });

  it("labels the total as unsaved while the editor is dirty", async () => {
    const user = userEvent.setup();
    renderEditor();

    expect(await screen.findByText("Total")).toBeInTheDocument();

    const qty = await screen.findByDisplayValue("2");
    await user.clear(qty);
    await user.type(qty, "3");

    expect(await screen.findByText("Total (unsaved)")).toBeInTheDocument();
  });

  it("counts max-from-free-stock against unallocated material only", async () => {
    renderEditor();
    // 100 on hand, 10 allocated, 2 per unit -> 45, not 50.
    expect(await screen.findByText("45")).toBeInTheDocument();
  });
});
