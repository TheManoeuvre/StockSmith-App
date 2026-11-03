import { describe, expect, it } from "vitest";
import {
  axisLabels,
  bucketSlots,
  bucketStart,
  gbp,
  gbpCompact,
  niceScale,
  percentChange,
  pointChange,
  resolveRange,
  toCsv,
} from "./insights";

describe("resolveRange", () => {
  const today = new Date(2026, 9, 25); // 25 Oct 2026

  it("makes a rolling range end today and compare with the equal period before it", () => {
    const r = resolveRange("30d", today);
    expect(r.start).toBe("2026-09-26");
    expect(r.end).toBe("2026-10-25");
    expect(r.compare).toEqual({ start: "2026-08-27", end: "2026-09-25" });
    expect(r.fullYear).toBeNull();
  });

  it("makes This year run from 1 Jan and compare with the same dates last year", () => {
    const r = resolveRange("year", today);
    expect(r.start).toBe("2026-01-01");
    expect(r.end).toBe("2026-10-25");
    expect(r.compare).toEqual({ start: "2025-01-01", end: "2025-10-25" });
    expect(r.fullYear).toEqual({ start: "2026-01-01", end: "2026-12-31" });
    expect(r.previousFullYear).toEqual({ start: "2025-01-01", end: "2025-12-31" });
  });

  it("falls back to 28 Feb when last year had no 29 Feb", () => {
    const r = resolveRange("year", new Date(2028, 1, 29));
    expect(r.compare.end).toBe("2027-02-28");
  });
});

describe("buckets", () => {
  it("starts weeks on Monday and months on the 1st", () => {
    expect(bucketStart("2026-03-04", "week")).toBe("2026-03-02"); // a Wednesday
    expect(bucketStart("2026-03-01", "week")).toBe("2026-02-23"); // a Sunday
    expect(bucketStart("2026-03-19", "month")).toBe("2026-03-01");
  });

  it("lists every bucket across a range, including a partial first one", () => {
    expect(bucketSlots("2026-03-04", "2026-03-22", "week")).toEqual(["2026-03-02", "2026-03-09", "2026-03-16"]);
    expect(bucketSlots("2026-01-15", "2026-03-02", "month")).toEqual(["2026-01-01", "2026-02-01", "2026-03-01"]);
  });

  it("labels a monthly axis with the first slot of each month", () => {
    const slots = bucketSlots("2026-01-01", "2026-12-31", "week");
    const labels = axisLabels(slots, "week", true);
    expect(labels.map((l) => l.label)).toEqual(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]);
  });
});

describe("formatting", () => {
  it("shows an unknown figure as a dash, never as zero", () => {
    expect(gbp(null)).toBe("-");
    expect(gbp("0")).toBe("£0.00");
  });

  it("puts a real minus sign and thousands separators on money", () => {
    expect(gbp("12480")).toBe("£12,480.00");
    expect(gbp("-212.4")).toBe("−£212.40");
    expect(gbpCompact(12480)).toBe("£12k");
    expect(gbpCompact(1480)).toBe("£1.5k");
  });
});

describe("deltas", () => {
  it("states direction and size in words as well as tone", () => {
    expect(percentChange(110, 100)).toEqual({ text: "▲ 10.0%", tone: "good" });
    expect(percentChange(90, 100)).toEqual({ text: "▼ 10.0%", tone: "bad" });
  });

  it("flips the tone when lower is better", () => {
    expect(percentChange(3, 2, true)?.tone).toBe("bad");
    expect(pointChange(2.6, 3.0, true)).toEqual({ text: "▼ 0.4 pts", tone: "good" });
  });

  it("has nothing to say without a usable comparison", () => {
    expect(percentChange(5, 0)).toBeNull();
    expect(percentChange(null, 5)).toBeNull();
  });
});

describe("niceScale", () => {
  it("rounds the top up to a tidy number", () => {
    const s = niceScale(0, 1180);
    expect(s.high).toBeGreaterThanOrEqual(1180);
    expect(s.values[0]).toBe(0);
    expect(s.values.length).toBeLessThanOrEqual(7);
  });

  it("keeps a negative low in view", () => {
    expect(niceScale(-50, 100).low).toBeLessThan(0);
  });
});

describe("toCsv", () => {
  it("quotes cells that need it", () => {
    expect(toCsv(["a", "b"], [["x,y", 'say "hi"']])).toBe('a,b\r\n"x,y","say ""hi"""');
  });
});
