import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useEditableCopy } from "./useEditableCopy";

interface Line {
  material_id: number;
  qty_required: string;
}

const bomOf = (qty: string): Line[] => [{ material_id: 1, qty_required: qty }];

function renderCopy(initialSeed: Line[] | undefined, seedKey: string | number = "p1") {
  return renderHook(
    ({ seed, seedKey }: { seed: Line[] | undefined; seedKey: string | number }) =>
      useEditableCopy<Line[]>({ key: "bom", label: "Build BOM", initial: [], seed, seedKey }),
    { initialProps: { seed: initialSeed, seedKey } }
  );
}

describe("useEditableCopy", () => {
  it("holds the initial value until a seed arrives", () => {
    const { result } = renderCopy(undefined);
    expect(result.current.value).toEqual([]);
    expect(result.current.isSeeded).toBe(false);
    expect(result.current.isDirty).toBe(false);
  });

  it("seeds once the server data lands", () => {
    const { result, rerender } = renderCopy(undefined);
    rerender({ seed: bomOf("2"), seedKey: "p1" });
    expect(result.current.value).toEqual(bomOf("2"));
    expect(result.current.isSeeded).toBe(true);
    expect(result.current.isDirty).toBe(false);
  });

  it("does NOT clobber unsaved edits when the same entity refetches", () => {
    // The regression this hook exists for. Every editor used to mirror its query into state
    // with an effect keyed on the query result, so any refetch — and `["products", id]` is
    // invalidated by nearly every save on the page — silently discarded in-progress edits.
    const { result, rerender } = renderCopy(bomOf("2"));
    act(() => result.current.setValue(bomOf("99")));
    expect(result.current.isDirty).toBe(true);

    // A background refetch delivers a new array identity, same entity.
    rerender({ seed: bomOf("2"), seedKey: "p1" });

    expect(result.current.value).toEqual(bomOf("99"));
    expect(result.current.isDirty).toBe(true);
  });

  it("re-seeds when the entity itself changes", () => {
    const { result, rerender } = renderCopy(bomOf("2"));
    act(() => result.current.setValue(bomOf("99")));

    rerender({ seed: bomOf("7"), seedKey: "p2" });

    expect(result.current.value).toEqual(bomOf("7"));
    expect(result.current.isDirty).toBe(false);
  });

  it("clears dirty on markSaved without needing a refetch", () => {
    // Since a re-seed can no longer happen, "saved" has to be stated explicitly.
    const { result } = renderCopy(bomOf("2"));
    act(() => result.current.setValue(bomOf("5")));
    expect(result.current.isDirty).toBe(true);

    act(() => result.current.markSaved());

    expect(result.current.isDirty).toBe(false);
    expect(result.current.value).toEqual(bomOf("5"));
  });

  it("adopts the server's canonical value when markSaved is given one", () => {
    // Guards against "1" vs "1.000": without this the editor looks dirty the moment the
    // normalised value comes back.
    const { result } = renderCopy(bomOf("2"));
    act(() => result.current.setValue(bomOf("5")));

    act(() => result.current.markSaved(bomOf("5.000")));

    expect(result.current.value).toEqual(bomOf("5.000"));
    expect(result.current.isDirty).toBe(false);
  });

  it("reports clean again when an edit is manually undone", () => {
    const { result } = renderCopy(bomOf("2"));
    act(() => result.current.setValue(bomOf("3")));
    expect(result.current.isDirty).toBe(true);
    act(() => result.current.setValue(bomOf("2")));
    expect(result.current.isDirty).toBe(false);
  });

  it("excludes projected-away fields from the comparison", () => {
    // PricingSection keeps and sends platformFeePercent even when a calculated fee source
    // hides the input; without `project` the form would report changes to an invisible field.
    interface Fields {
      salePrice: string;
      platformFeePercent: string;
    }
    const seed: Fields = { salePrice: "10", platformFeePercent: "5" };
    const { result } = renderHook(() =>
      useEditableCopy<Fields>({
        key: "price",
        label: "Pricing",
        initial: seed,
        seed,
        seedKey: 1,
        project: (v) => ({ salePrice: v.salePrice }),
      })
    );

    act(() => result.current.setValue({ salePrice: "10", platformFeePercent: "99" }));
    expect(result.current.isDirty).toBe(false);

    act(() => result.current.setValue({ salePrice: "12", platformFeePercent: "99" }));
    expect(result.current.isDirty).toBe(true);
  });
});
