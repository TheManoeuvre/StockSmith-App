import { describe, expect, it } from "vitest";
import { deepEqual } from "./deepEqual";

describe("deepEqual", () => {
  it("compares primitives", () => {
    expect(deepEqual(1, 1)).toBe(true);
    expect(deepEqual("a", "a")).toBe(true);
    expect(deepEqual(1, "1")).toBe(false);
    expect(deepEqual(null, undefined)).toBe(false);
    expect(deepEqual(NaN, NaN)).toBe(true); // Object.is semantics
  });

  it("ignores key order", () => {
    // The reason this exists instead of JSON.stringify: BomOverrideEditor builds its
    // additive lines at two sites whose object literals list keys in different orders.
    expect(deepEqual({ a: 1, b: 2 }, { b: 2, a: 1 })).toBe(true);
  });

  it("compares arrays elementwise and by length", () => {
    expect(deepEqual([1, 2, 3], [1, 2, 3])).toBe(true);
    expect(deepEqual([1, 2], [1, 2, 3])).toBe(false);
    expect(deepEqual([1, 2, 3], [3, 2, 1])).toBe(false); // order matters for arrays
    expect(deepEqual([], {})).toBe(false);
  });

  it("recurses through nested structures", () => {
    const bom = [
      { material_id: 1, qty_required: "2" },
      { material_id: 2, qty_required: "0.5" },
    ];
    expect(deepEqual(bom, structuredClone(bom))).toBe(true);
    expect(deepEqual(bom, [{ material_id: 1, qty_required: "3" }, bom[1]])).toBe(false);
  });

  it("treats a missing key and an undefined value as different", () => {
    expect(deepEqual({ a: 1 }, { a: 1, b: undefined })).toBe(false);
  });

  it("handles number-keyed records (the override maps)", () => {
    expect(deepEqual({ 1: { mode: "inherit" } }, { 1: { mode: "inherit" } })).toBe(true);
    expect(deepEqual({ 1: { mode: "inherit" } }, { 1: { mode: "qty" } })).toBe(false);
  });
});
