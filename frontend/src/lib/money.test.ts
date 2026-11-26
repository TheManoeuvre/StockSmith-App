import { afterEach, describe, expect, it } from "vitest";
import { currencySymbol, formatAmount, formatMoney, formatUnitCost, setDisplayCurrency } from "./money";

afterEach(() => setDisplayCurrency("GBP"));

describe("display currency", () => {
  it("defaults to pounds", () => {
    expect(formatAmount(12.5)).toBe("£12.50");
  });

  it("follows the setting everywhere that formats an amount", () => {
    setDisplayCurrency("USD");
    expect(formatAmount(12.5)).toBe("$12.50");
    expect(formatUnitCost("0.004")).toBe("$0.01");
    setDisplayCurrency("EUR");
    expect(formatAmount("3")).toBe("€3.00");
    expect(currencySymbol()).toBe("€");
  });

  it("shows a missing amount as a dash, never as zero", () => {
    expect(formatAmount(null)).toBe("-");
    expect(formatAmount(undefined)).toBe("-");
    expect(formatMoney(null, "GBP")).toBe("-");
  });

  it("keeps an order's own currency, and falls back to the display currency", () => {
    setDisplayCurrency("EUR");
    expect(formatMoney("10", "USD")).toBe("$10.00");
    expect(formatMoney("10", null)).toBe("€10.00");
  });

  it("spaces out a code with no symbol so it can't glue to the number", () => {
    expect(formatMoney("10", "XYZ")).toBe("XYZ 10.00");
  });
});
