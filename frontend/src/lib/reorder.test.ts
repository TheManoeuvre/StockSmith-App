import { describe, expect, it } from "vitest";
import type { Material } from "../api/types";
import {
  coverOnArrival,
  coverTone,
  projectedOnHand,
  reorderGranularity,
  suggestReorderQty,
} from "./reorder";

const NOW = new Date("2026-09-04T00:00:00Z");
const inDays = (n: number) =>
  new Date(NOW.getTime() + n * 86_400_000).toISOString().slice(0, 10);

function mat(overrides: Partial<Material>): Material {
  return {
    unit: "g",
    current_qty: "0",
    reorder_threshold: "0",
    consumption_rate_per_week: null,
    on_order_qty: null,
    typical_reorder_qty: null,
    ...overrides,
  } as Material;
}

describe("coverOnArrival", () => {
  it("returns null when there is no consumption rate to divide by", () => {
    expect(
      coverOnArrival(mat({ current_qty: "500" }), inDays(7), 1000, 5, NOW),
    ).toBeNull();
    expect(
      coverOnArrival(
        mat({ current_qty: "500", consumption_rate_per_week: "0" }),
        inDays(7),
        1000,
        5,
        NOW,
      ),
    ).toBeNull();
  });

  it("runs stock down over the lead time before adding the delivery back", () => {
    // 700 on hand, 70/wk = 10/day. 7 days to arrival → 70 consumed, 630 left, +0 inbound.
    const bare = coverOnArrival(
      mat({ current_qty: "700", consumption_rate_per_week: "70" }),
      inDays(7),
      0,
      null,
      NOW,
    );
    expect(bare).toBeCloseTo(9); // 630 / 70

    // Same, but this PO brings 700 more → 1330 / 70 = 19 weeks.
    const withPo = coverOnArrival(
      mat({ current_qty: "700", consumption_rate_per_week: "70" }),
      inDays(7),
      700,
      null,
      NOW,
    );
    expect(withPo).toBeCloseTo(19);
  });

  it("lifts a thin cover past the amber line once the PO quantity is counted", () => {
    const m = mat({ current_qty: "100", consumption_rate_per_week: "100" });
    expect(coverOnArrival(m, inDays(7), 0, null, NOW)!).toBeLessThan(2);
    expect(coverOnArrival(m, inDays(7), 500, null, NOW)!).toBeGreaterThan(4);
  });

  it("folds in what is already on order", () => {
    const m = mat({
      current_qty: "0",
      consumption_rate_per_week: "50",
      on_order_qty: "200",
    });
    // no time lost (arrival today), 200 already inbound + 50 from this PO → 250 / 50 = 5.
    expect(coverOnArrival(m, inDays(0), 50, null, NOW)).toBeCloseTo(5);
  });

  it("uses the supplier lead time when no arrival date is given", () => {
    const m = mat({ current_qty: "100", consumption_rate_per_week: "70" });
    // 10 business/calendar days here (helper is calendar) → 100/day rate... 70/7=10/day,
    // 10 days → 100 consumed, 0 left, +140 → 2 weeks.
    expect(coverOnArrival(m, null, 140, 10, NOW)).toBeCloseTo(2);
  });
});

describe("projectedOnHand", () => {
  it("is plain on-hand when there is no rate", () => {
    expect(projectedOnHand(mat({ current_qty: "250" }), inDays(30), 5, NOW)).toBe(250);
  });

  it("never goes below zero", () => {
    expect(
      projectedOnHand(
        mat({ current_qty: "10", consumption_rate_per_week: "70" }),
        inDays(30),
        null,
        NOW,
      ),
    ).toBe(0);
  });
});

describe("reorderGranularity", () => {
  it("is 1 for each and 100 for bulk units", () => {
    expect(reorderGranularity("each")).toBe(1);
    expect(reorderGranularity("g")).toBe(100);
    expect(reorderGranularity("ml")).toBe(100);
  });
});

describe("suggestReorderQty", () => {
  it("uses typical_reorder_qty verbatim when the material has one", () => {
    expect(
      suggestReorderQty(mat({ typical_reorder_qty: "2500", unit: "g" })),
    ).toBe(2500);
  });

  it("clears the threshold, rounded up to 100 for bulk", () => {
    // threshold 1000, on hand 240 → need 760 → round up to 800.
    expect(
      suggestReorderQty(
        mat({ unit: "g", reorder_threshold: "1000", current_qty: "240" }),
      ),
    ).toBe(800);
  });

  it("covers ~8 weeks of consumption when that is the larger gap", () => {
    // rate 200/wk → 1600 for 8 weeks, nothing on hand, threshold small → 1600.
    expect(
      suggestReorderQty(
        mat({
          unit: "g",
          reorder_threshold: "100",
          current_qty: "0",
          consumption_rate_per_week: "200",
        }),
      ),
    ).toBe(1600);
  });

  it("rounds up to whole units for 'each' and never suggests zero", () => {
    expect(
      suggestReorderQty(
        mat({ unit: "each", reorder_threshold: "5", current_qty: "3" }),
      ),
    ).toBe(2);
    expect(
      suggestReorderQty(
        mat({ unit: "each", reorder_threshold: "0", current_qty: "0" }),
      ),
    ).toBe(1);
  });
});

describe("coverTone", () => {
  it("bands at 2 and 4 weeks", () => {
    expect(coverTone(null)).toBe("muted");
    expect(coverTone(1.9)).toBe("red");
    expect(coverTone(2)).toBe("amber");
    expect(coverTone(3.9)).toBe("amber");
    expect(coverTone(4)).toBe("green");
  });
});
