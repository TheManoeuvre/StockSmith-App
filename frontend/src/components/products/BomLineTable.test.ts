import { describe, expect, it } from "vitest";
import { computeLineCosts } from "./BomLineTable";
import type { Material } from "../../api/types";

const material = (id: number, avg_unit_cost: string): Material =>
  ({ id, avg_unit_cost, name: `M${id}`, current_qty: "100", allocated_qty: "0" }) as unknown as Material;

describe("computeLineCosts", () => {
  it("multiplies qty by the material's current average unit cost", () => {
    const { perLine, total } = computeLineCosts(
      [
        { material_id: 1, qty_required: "2" },
        { material_id: 2, qty_required: "0.5" },
      ],
      [material(1, "1.50"), material(2, "4.00")]
    );
    expect(perLine[0].cost).toBe(3);
    expect(perLine[1].cost).toBe(2);
    expect(total).toBe(5);
  });

  it("totals raw values, not per-row rounded ones", () => {
    // Three lines at 0.005 each. Rounded up per row (formatUnitCost's ceiling) they'd read
    // as £0.01 apiece and the total would look like £0.03; the honest total is £0.015 -> £0.02.
    // This is why the table formats with formatMoney and sums before formatting.
    const { total } = computeLineCosts(
      [
        { material_id: 1, qty_required: "1" },
        { material_id: 2, qty_required: "1" },
        { material_id: 3, qty_required: "1" },
      ],
      [material(1, "0.005"), material(2, "0.005"), material(3, "0.005")]
    );
    expect(total).toBeCloseTo(0.015, 10);
    expect(total).not.toBe(0.03);
  });

  it("gives each line its share of the total", () => {
    const { perLine } = computeLineCosts(
      [
        { material_id: 1, qty_required: "3" },
        { material_id: 2, qty_required: "1" },
      ],
      [material(1, "1.00"), material(2, "1.00")]
    );
    expect(perLine[0].share).toBe(75);
    expect(perLine[1].share).toBe(25);
    expect(perLine[0].share! + perLine[1].share!).toBeCloseTo(100, 10);
  });

  it("reports an unknown material as null rather than zero", () => {
    const { perLine, total } = computeLineCosts(
      [
        { material_id: 1, qty_required: "2" },
        { material_id: 99, qty_required: "2" },
      ],
      [material(1, "1.00")]
    );
    expect(perLine[0].cost).toBe(2);
    expect(perLine[1].cost).toBeNull();
    expect(perLine[1].share).toBeNull();
    // The known line still contributes — a missing material shouldn't blank the whole table.
    expect(total).toBe(2);
  });

  it("has no total and no shares when nothing is costable", () => {
    const { perLine, total } = computeLineCosts([{ material_id: 1, qty_required: "2" }], []);
    expect(total).toBeNull();
    expect(perLine[0].share).toBeNull();
  });

  it("gives no shares when the total is zero", () => {
    const { perLine, total } = computeLineCosts(
      [{ material_id: 1, qty_required: "2" }],
      [material(1, "0")]
    );
    expect(total).toBe(0);
    expect(perLine[0].share).toBeNull(); // not NaN or Infinity
  });

  it("handles an empty BOM", () => {
    expect(computeLineCosts([], [material(1, "1.00")])).toEqual({ perLine: [], total: null });
  });
});
