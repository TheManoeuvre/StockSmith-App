import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { ReceiveDialog } from "./ReceiveDialog";
import type { Material, Purchase } from "../../api/types";

const MATERIALS = [
  { id: 1, name: "PLA Black", unit: "g", category: "filament" },
  { id: 2, name: "Mail Bag", unit: "each", category: "packaging" },
] as unknown as Material[];

function purchase(overrides: Partial<Purchase> = {}): Purchase {
  return {
    id: 7,
    supplier_id: null,
    supplier_name: null,
    order_date: "2026-01-01",
    expected_arrival_date: null,
    status: "ordered",
    received_at: null,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    lines: [
      {
        id: 11,
        purchase_id: 7,
        material_id: 1,
        qty: "1000",
        total_cost: "20.00",
        notes: null,
        closed_at: null,
        received_qty: "400",
        outstanding_qty: "600",
        receipts: [],
      },
      {
        id: 12,
        purchase_id: 7,
        material_id: 2,
        qty: "50",
        total_cost: "5.00",
        notes: null,
        closed_at: null,
        received_qty: "0",
        outstanding_qty: "50",
        receipts: [],
      },
    ],
    ...overrides,
  } as Purchase;
}

function renderDialog(overrides: Partial<Purchase> = {}) {
  const onSubmit = vi.fn();
  render(
    <ReceiveDialog
      purchase={purchase(overrides)}
      materials={MATERIALS}
      busy={false}
      error={null}
      onSubmit={onSubmit}
      onClose={() => {}}
    />,
  );
  return { onSubmit };
}

it("prefills each line with what is still outstanding, not what was ordered", () => {
  renderDialog();
  expect(screen.getByLabelText("Receiving now for PLA Black")).toHaveValue(600);
  expect(screen.getByLabelText("Receiving now for Mail Bag")).toHaveValue(50);
});

it("sends one delivery for however many lines arrived in it", async () => {
  const user = userEvent.setup();
  const { onSubmit } = renderDialog();

  await user.click(screen.getByRole("button", { name: /Record 2 lines/ }));

  expect(onSubmit).toHaveBeenCalledTimes(1);
  const [, lines] = onSubmit.mock.calls[0];
  expect(lines).toEqual([
    { line_id: 11, qty: "600" },
    { line_id: 12, qty: "50" },
  ]);
});

it("leaves the cost out when it is blank, so the line total is shared pro-rata", async () => {
  const user = userEvent.setup();
  const { onSubmit } = renderDialog();

  // 600 of a 1000-unit line costing £20 is £12 — offered as the placeholder rather than
  // filled in, so the default is visible without being something to delete.
  expect(screen.getByLabelText("Cost of this delivery for PLA Black")).toHaveAttribute("placeholder", "12.00");

  await user.click(screen.getByRole("button", { name: /Record 2 lines/ }));
  expect(onSubmit.mock.calls[0][1][0]).not.toHaveProperty("total_cost");
});

it("will not submit more than is outstanding", async () => {
  const user = userEvent.setup();
  const { onSubmit } = renderDialog();

  const input = screen.getByLabelText("Receiving now for PLA Black");
  await user.clear(input);
  await user.type(input, "900");

  expect(screen.getByRole("button", { name: /Record/ })).toBeDisabled();
  expect(screen.getByText(/More than was ordered is outstanding/)).toBeInTheDocument();
  expect(onSubmit).not.toHaveBeenCalled();
});

it("warns before booking a delivery in at no cost", () => {
  // A draft purchase raised from a low-stock alert arrives with no cost on it, and
  // receiving one silently drags the material's average cost down.
  renderDialog({
    lines: [
      {
        id: 11,
        purchase_id: 7,
        material_id: 1,
        qty: "1000",
        total_cost: "0",
        notes: null,
        closed_at: null,
        received_qty: "0",
        outstanding_qty: "1000",
        receipts: [],
      },
    ],
  } as Partial<Purchase>);

  expect(screen.getByText(/pull the material's average\s+cost down/)).toBeInTheDocument();
});

it("only offers lines that still have something outstanding", () => {
  renderDialog({
    lines: [
      {
        id: 11,
        purchase_id: 7,
        material_id: 1,
        qty: "1000",
        total_cost: "20.00",
        notes: null,
        closed_at: null,
        received_qty: "1000",
        outstanding_qty: "0",
        receipts: [],
      },
      {
        id: 12,
        purchase_id: 7,
        material_id: 2,
        qty: "50",
        total_cost: "5.00",
        notes: null,
        closed_at: null,
        received_qty: "0",
        outstanding_qty: "50",
        receipts: [],
      },
    ],
  } as Partial<Purchase>);

  expect(screen.queryByLabelText("Receiving now for PLA Black")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Receiving now for Mail Bag")).toBeInTheDocument();
});
