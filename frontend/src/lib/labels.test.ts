import { beforeEach, describe, expect, it } from "vitest";
import {
  LARGE_LABEL,
  SMALL_LABEL,
  clampMm,
  describeSize,
  layoutFor,
  loadLabelSize,
  saveLabelSize,
  sizeFor,
  smallLabelShowsCode,
  trimmedPath,
} from "./labels";

beforeEach(() => window.localStorage.clear());

describe("label sizes", () => {
  it("defaults to 51 x 25 mm and offers 6 x 4 in as a preset", () => {
    expect(loadLabelSize()).toEqual(SMALL_LABEL);
    expect(LARGE_LABEL.widthMm).toBeCloseTo(152.4);
    expect(LARGE_LABEL.heightMm).toBeCloseTo(101.6);
    expect(describeSize(SMALL_LABEL)).toBe("51 × 25 mm");
    expect(describeSize(LARGE_LABEL)).toBe("6 × 4 in");
  });

  it("remembers the choice, including a custom size", () => {
    saveLabelSize(LARGE_LABEL);
    expect(loadLabelSize()).toEqual(LARGE_LABEL);
    saveLabelSize({ preset: "custom", widthMm: 70, heightMm: 40 });
    expect(loadLabelSize()).toEqual({ preset: "custom", widthMm: 70, heightMm: 40 });
    expect(describeSize(loadLabelSize())).toBe("70 × 40 mm");
  });

  it("ignores a saved value it can't read", () => {
    window.localStorage.setItem("stocksmith-label-size", "{not json");
    expect(loadLabelSize()).toEqual(SMALL_LABEL);
    window.localStorage.setItem("stocksmith-label-size", JSON.stringify({ preset: "custom", widthMm: "wide" }));
    expect(loadLabelSize()).toEqual(SMALL_LABEL);
  });

  it("keeps a custom size printable", () => {
    expect(clampMm(5)).toBe(20);
    expect(clampMm(900)).toBe(300);
    expect(clampMm(Number.NaN)).toBe(20);
    window.localStorage.setItem("stocksmith-label-size", JSON.stringify({ preset: "custom", widthMm: 1, heightMm: 999 }));
    expect(loadLabelSize()).toEqual({ preset: "custom", widthMm: 20, heightMm: 300 });
  });

  it("switching to Custom starts from the size that was showing", () => {
    expect(sizeFor("custom", LARGE_LABEL)).toEqual({ preset: "custom", widthMm: 152.4, heightMm: 101.6 });
    expect(sizeFor("small")).toEqual(SMALL_LABEL);
  });

  it("uses the roomy layout from about 100 mm wide", () => {
    expect(layoutFor(SMALL_LABEL)).toBe("small");
    expect(layoutFor(LARGE_LABEL)).toBe("large");
    expect(layoutFor({ preset: "custom", widthMm: 99, heightMm: 60 })).toBe("small");
    expect(layoutFor({ preset: "custom", widthMm: 100, heightMm: 60 })).toBe("large");
  });
});

describe("the path on the small label", () => {
  it("shows a short path whole", () => {
    expect(trimmedPath(["Workshop", "Oils rack"])).toBe("Workshop › Oils rack");
  });

  it("keeps the worst case from the brief on one go", () => {
    expect(trimmedPath(["Warehouse Annexe", "Rack 04", "Shelf C"])).toBe("Warehouse Annexe › Rack 04 › Shelf C");
  });

  it("drops the outermost ancestors first, so the nearest parent always survives", () => {
    const path = trimmedPath(["Warehouse Annexe", "Mezzanine", "Rack 04", "Shelf C", "Tray 2", "Left side", "Row nine"]);
    expect(path.startsWith("… › ")).toBe(true);
    expect(path.endsWith("Row nine")).toBe(true);
    expect(path).not.toContain("Warehouse Annexe");
    expect(path.length).toBeLessThanOrEqual(62);
  });

  it("never drops the last ancestor, however long it is", () => {
    expect(trimmedPath(["A".repeat(100)])).toBe(`… › ${"A".repeat(100)}`);
  });

  it("shows the readable code only when the path is short enough to leave room", () => {
    expect(smallLabelShowsCode("Workshop › Oils rack")).toBe(true);
    expect(smallLabelShowsCode("Warehouse Annexe › Rack 04 › Shelf C")).toBe(false);
    expect(smallLabelShowsCode("")).toBe(false);
  });
});
