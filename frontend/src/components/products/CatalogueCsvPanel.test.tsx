import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";

const exportVariantsCsv = vi.fn(() => Promise.resolve());
const importBomsCsv = vi.fn(() => Promise.resolve({ created: 1, updated: 0, failed: [{ row: 3, error: "no material is called 'X'" }] }));
vi.mock("../../api/products", () => ({
  productsApi: {
    exportCsv: vi.fn(() => Promise.resolve()),
    importCsv: vi.fn(),
    exportVariantsCsv,
    importVariantsCsv: vi.fn(),
    exportBomsCsv: vi.fn(() => Promise.resolve()),
    importBomsCsv,
  },
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="/help">{children}</a>,
}));

const { CatalogueCsvPanel } = await import("./CatalogueCsvPanel");

function renderPanel() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CatalogueCsvPanel />
    </QueryClientProvider>,
  );
}

it("offers the three files in import order", () => {
  renderPanel();
  expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
    "1. Products",
    "2. Variants",
    "3. Recipes (BOMs)",
  ]);
});

it("wires each file to its own export and import, and shows row errors", async () => {
  renderPanel();
  const variants = screen.getByRole("heading", { name: "2. Variants" }).parentElement!;
  fireEvent.click(within(variants).getByRole("button", { name: "Export CSV" }));
  await waitFor(() => expect(exportVariantsCsv).toHaveBeenCalled());

  const boms = screen.getByRole("heading", { name: "3. Recipes (BOMs)" }).parentElement!;
  const input = boms.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(["product_sku,material,qty_required\n"], "boms.csv", { type: "text/csv" });
  fireEvent.change(input, { target: { files: [file] } });
  expect(await within(boms).findByText(/Row 3: no material is called 'X'/)).toBeInTheDocument();
  expect(importBomsCsv).toHaveBeenCalled();
});
