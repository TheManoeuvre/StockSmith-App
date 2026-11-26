import { describe, expect, it } from "vitest";
import type { StockTakeLine } from "../api/types";
import { stockChanges } from "./stockChange";

const line = (id: number, o: Partial<StockTakeLine> = {}) =>
  ({
    id,
    material_id: 1,
    product_id: null,
    variant_id: null,
    location_id: id,
    expected_qty: "20",
    status: "pending",
    unassigned_qty: "12",
    ...o,
  }) as StockTakeLine;

describe("stockChanges", () => {
  it("extra found comes out of unplaced stock first", () => {
    expect(stockChanges([line(1)], { 1: "25" }).get(1)).toBe(0);
    expect(stockChanges([line(1)], { 1: "40" }).get(1)).toBe(8);
  });

  it("shares one item's unplaced stock between its places", () => {
    const out = stockChanges([line(2), line(1)], { 1: "28", 2: "28" });
    expect([out.get(1), out.get(2)]).toEqual([0, 4]);
  });

  it("a shortfall comes off the total", () => {
    expect(stockChanges([line(1)], { 1: "18" }).get(1)).toBe(-2);
  });

  it("an applied line reports what happened, and a held one says nothing", () => {
    expect(stockChanges([line(1, { status: "applied", stock_change: "0" })], { 1: "25" }).get(1)).toBe(0);
    expect(stockChanges([line(1, { status: "conflict" })], { 1: "25" }).get(1)).toBeNull();
  });

  it("an ordinary take changes by the difference", () => {
    expect(stockChanges([line(1, { location_id: null, unassigned_qty: null })], { 1: "25" }).get(1)).toBe(5);
  });
});
