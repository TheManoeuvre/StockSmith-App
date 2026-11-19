import { afterEach, describe, expect, it } from "vitest";
import {
  displayQty,
  formatDayMonthRange,
  isWholeNumberUnit,
  normalizeQtyForUnit,
  qtyWithUnit,
  roundQty,
  sellableSummary,
  setUnitRules,
  unitSuffix,
  wholeNumberStepFor,
  type SellableInput,
} from "./format";

describe("displayQty", () => {
  it("drops the trailing zeros the backend's Numeric(14,4) carries", () => {
    expect(displayQty("30.0000")).toBe("30");
  });

  it("keeps a genuine fraction", () => {
    expect(displayQty("12.5000")).toBe("12.5");
    expect(displayQty("13.4500")).toBe("13.45");
  });

  it("treats null/blank as zero and leaves non-numbers alone", () => {
    expect(displayQty(null)).toBe("0");
    expect(displayQty("")).toBe("0");
    expect(displayQty("n/a")).toBe("n/a");
  });
});

describe("qtyWithUnit", () => {
  it("appends g/ml but never 'each'", () => {
    expect(qtyWithUnit("100.0000", "g")).toBe("100 g");
    expect(qtyWithUnit("250.0000", "ml")).toBe("250 ml");
    expect(qtyWithUnit("10.0000", "each")).toBe("10");
    expect(qtyWithUnit("10.0000", null)).toBe("10");
  });
});

// Numbers from a real product ("Brick Separator Themed Bottle Opener", variant Orange):
// 3 on hand, none reserved, 97 buildable, ceiling of 20 on the parent product.
const ORANGE: SellableInput = {
  current_stock: 3,
  allocated_qty: 0,
  max_buildable: 97,
  expected_max_buildable: 97,
  max_buildable_incl_fallbacks: 97,
  expected_max_buildable_incl_fallbacks: 97,
  max_sellable: 3,
  max_sellable_reason: "stock",
  expected_max_sellable: 20,
  theoretical_max_sellable: 20,
  theoretical_max_sellable_reason: "ceiling",
};

describe("sellableSummary", () => {
  it("leads with the figure that gets pushed, and explains the cap", () => {
    const s = sellableSummary(ORANGE, { pushBuildableCapacity: true, platformCeilingQty: 20 });
    expect(s.headline).toBe(20);
    expect(s.builtFree).toBe(3);
    expect(s.buildable).toBe(97);
    expect(s.capLabel).toBe("capped at 20");
  });

  it("falls back to already-built stock when buildable capacity isn't pushed", () => {
    const s = sellableSummary(ORANGE, { pushBuildableCapacity: false, platformCeilingQty: 20 });
    expect(s.headline).toBe(3);
    // "stock" isn't a cap — it just means the headline already equals what's built. The
    // old UI called this "(nothing built)" even with three on the shelf.
    expect(s.capLabel).toBeNull();
  });

  it("names packaging when packaging is what's binding", () => {
    const s = sellableSummary(
      { ...ORANGE, theoretical_max_sellable: 6, theoretical_max_sellable_reason: "packaging" },
      { pushBuildableCapacity: true }
    );
    expect(s.headline).toBe(6);
    expect(s.capLabel).toBe("limited by packaging");
  });

  it("says nothing about materials — that's the headline, not a constraint on it", () => {
    const s = sellableSummary(
      { ...ORANGE, theoretical_max_sellable: 100, theoretical_max_sellable_reason: "materials" },
      { pushBuildableCapacity: true }
    );
    expect(s.capLabel).toBeNull();
  });

  describe("expected", () => {
    it("is suppressed when nothing on order raises the ceiling", () => {
      expect(sellableSummary(ORANGE, { pushBuildableCapacity: true }).expected).toBeNull();
    });

    it("adds free stock, so it stays comparable with the current figure", () => {
      // The backend's expected_max_sellable is materials-and-packaging only. Left raw it
      // would read 40 against a current figure of 43 — as if capacity shrank.
      const s = sellableSummary(
        {
          ...ORANGE,
          expected_max_buildable: 140,
          expected_max_buildable_incl_fallbacks: 140,
          expected_max_sellable: 140,
          theoretical_max_sellable: 100,
        },
        { pushBuildableCapacity: true }
      );
      expect(s.expected).toBe(143);
    });

    it("stays under the platform ceiling", () => {
      const s = sellableSummary(
        { ...ORANGE, expected_max_buildable: 140, expected_max_buildable_incl_fallbacks: 140, expected_max_sellable: 140 },
        { pushBuildableCapacity: true, platformCeilingQty: 20 }
      );
      expect(s.expected).toBe(20);
    });

    it("is null when there's no BOM to build from", () => {
      const s = sellableSummary(
        {
          ...ORANGE,
          max_buildable: null,
          expected_max_buildable: null,
          max_buildable_incl_fallbacks: null,
          expected_max_buildable_incl_fallbacks: null,
        },
        { pushBuildableCapacity: true }
      );
      expect(s.expected).toBeNull();
      expect(s.buildable).toBeNull();
    });
  });
});

describe("units added in Settings", () => {
  const BUILT_INS = [
    { name: "g", decimal_places: null, show_suffix: true },
    { name: "ml", decimal_places: null, show_suffix: true },
    { name: "each", decimal_places: 0, show_suffix: false },
  ];
  const withMetres = () => setUnitRules([...BUILT_INS, { name: "m", decimal_places: 2, show_suffix: true }]);
  afterEach(() => setUnitRules(BUILT_INS));

  it("shows a two-decimal unit to exactly two places, with its name", () => {
    withMetres();
    expect(qtyWithUnit("1.5", "m")).toBe("1.50 m");
    expect(qtyWithUnit("12.0000", "m")).toBe("12.00 m");
  });

  it("keeps the trimmed display for an unbounded unit and the bare number for a hidden one", () => {
    withMetres();
    expect(qtyWithUnit("12.5000", "g")).toBe("12.5 g");
    expect(qtyWithUnit("3.0000", "each")).toBe("3");
    expect(unitSuffix("each")).toBe("");
  });

  it("steps and normalises by the unit's own precision", () => {
    withMetres();
    expect(wholeNumberStepFor("m")).toBe("0.01");
    expect(wholeNumberStepFor("each")).toBe("1");
    expect(wholeNumberStepFor("g")).toBe("any");
    expect(normalizeQtyForUnit("1.5", "m")).toBe("1.50");
    expect(normalizeQtyForUnit("2.6", "each")).toBe("3");
    expect(normalizeQtyForUnit("2.6", "g")).toBe("2.6");
    expect(isWholeNumberUnit("each")).toBe(true);
    expect(isWholeNumberUnit("m")).toBe(false);
  });

  it("does not round a bounded unit's quantity to a whole number", () => {
    withMetres();
    expect(roundQty("1.5", "m")).toBe("1.50");
    expect(roundQty("1.5", "g")).toBe("2");
    expect(roundQty("1.5")).toBe("2");
  });

  it("falls back to showing the name for a unit it has no row for", () => {
    expect(unitSuffix("furlong")).toBe(" furlong");
  });
});

describe("formatDayMonthRange", () => {
  const span = ["2026-08-18T12:00:00Z", null, "2026-08-15T12:00:00Z"];

  // Intl puts thin spaces around the dash; compare on ordinary ones.
  const plain = (s: string) => s.replace(/\s+/g, " ");

  it("writes a range the way the computer's locale does", () => {
    expect(plain(formatDayMonthRange(span, "en-GB"))).toBe("15 – 18 Aug");
    expect(plain(formatDayMonthRange(span, "en-US"))).toBe("Aug 15 – 18");
  });

  it("collapses to one date when every order falls on the same day", () => {
    expect(formatDayMonthRange(["2026-08-15T09:00:00Z", "2026-08-15T17:00:00Z"], "en-GB")).toBe("15 Aug");
  });

  it("shows a dash when no order has a date", () => {
    expect(formatDayMonthRange([null, undefined])).toBe("-");
  });
});
