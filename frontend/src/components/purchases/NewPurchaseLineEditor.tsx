import { MaterialSelect } from "../materials/MaterialSelect";
import { Badge } from "../common/Badge";
import type { Material, PriceReference } from "../../api/types";
import type { PurchaseLineInput } from "../../api/purchases";
import { displayQty, formatDayMonth, normalizeQtyForUnit } from "../../lib/format";
import { coverOnArrival, coverTone, projectedOnHand, type CoverTone } from "../../lib/reorder";

const COVER_TEXT: Record<CoverTone, string> = {
  red: "text-red-600",
  amber: "text-amber-700",
  green: "text-emerald-700",
  muted: "text-slate-400",
};

/** The material's line total is keyed from the invoice; the unit price is derived from it.
 *  Sub-£1 unit prices get 4 dp so a per-gram figure isn't rounded to nothing. */
function unitPriceLabel(unitPrice: number, unit: Material["unit"]): string {
  if (!Number.isFinite(unitPrice) || unitPrice <= 0) return "—";
  const money = `£${unitPrice.toFixed(unitPrice < 1 ? 4 : 2)}`;
  return unit === "each" ? `${money} each` : `${money}/${unit}`;
}

/**
 * The order-lines editor for a *new* purchase. Distinct from the shared PurchaseLineEditor
 * (which carries received/outstanding columns and per-line close/reopen for an existing
 * order): here every line is a forecasting check — the derived unit price against what this
 * supplier last charged, and the weeks of cover the delivery buys on arrival.
 */
export function NewPurchaseLineEditor({
  materials,
  lines,
  onChange,
  priceByMaterial,
  expectedArrivalDate,
}: {
  materials: Material[];
  lines: PurchaseLineInput[];
  onChange: (lines: PurchaseLineInput[]) => void;
  priceByMaterial: Map<number, PriceReference>;
  expectedArrivalDate: string;
}) {
  const materialById = new Map(materials.map((m) => [m.id, m]));

  const updateLine = (i: number, patch: Partial<PurchaseLineInput>) =>
    onChange(lines.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  const removeLine = (i: number) => onChange(lines.filter((_, idx) => idx !== i));
  const addLine = () => {
    const firstUnused =
      materials.find((m) => !lines.some((l) => l.material_id === m.id)) ?? materials[0];
    if (!firstUnused) return;
    onChange([...lines, { material_id: firstUnused.id, qty: "0", total_cost: "0" }]);
  };

  return (
    <div className="overflow-hidden rounded border border-slate-200 bg-white shadow-sm">
      {lines.length === 0 ? (
        <p className="bg-amber-50 px-3 py-4 text-center text-[12.5px] text-amber-700">
          No lines yet — add one, or take a suggestion below
        </p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {lines.map((line, i) => {
            const material = materialById.get(line.material_id);
            const qty = Number(line.qty) || 0;
            const total = Number(line.total_cost) || 0;
            const unitPrice = qty > 0 ? total / qty : 0;

            const ref = priceByMaterial.get(line.material_id);
            const lastUnit = ref ? Number(ref.unit_cost) : null;
            const rose = lastUnit != null && lastUnit > 0 && unitPrice > lastUnit * 1.005;
            const deltaPct =
              lastUnit != null && lastUnit > 0 ? (unitPrice / lastUnit - 1) * 100 : null;

            const cover = material
              ? coverOnArrival(
                  material,
                  expectedArrivalDate || null,
                  qty,
                  material.lead_time_days,
                )
              : null;
            const tone = coverTone(cover);
            const onHandBy = material
              ? projectedOnHand(material, expectedArrivalDate || null, material.lead_time_days)
              : 0;

            return (
              <li key={line.id ?? `new-${i}`} className="px-3 py-2">
                <div className="flex items-center gap-2">
                  <span
                    className="h-3 w-3 shrink-0 rounded-sm border border-slate-200"
                    style={{ backgroundColor: material?.colour_hex ?? "#e2e8f0" }}
                  />
                  <MaterialSelect
                    className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
                    materials={materials}
                    value={line.material_id}
                    onChange={(material_id) => updateLine(i, { material_id })}
                  />
                  <input
                    type="number"
                    className="w-20 rounded border border-slate-300 px-2 py-1 text-right text-sm tabular-nums"
                    step={material?.unit === "each" ? "1" : "10"}
                    value={line.qty}
                    onChange={(e) => updateLine(i, { qty: e.target.value })}
                    onBlur={(e) =>
                      updateLine(i, {
                        qty: normalizeQtyForUnit(e.target.value, material?.unit),
                      })
                    }
                    aria-label="Quantity"
                  />
                  <span className="w-7 shrink-0 text-[11px] text-slate-400">
                    {material?.unit === "each" ? "" : material?.unit}
                  </span>
                  <span className="shrink-0 text-[11px] text-slate-400">£</span>
                  <input
                    type="number"
                    step="0.01"
                    className={`w-24 rounded border px-2 py-1 text-right text-sm font-semibold tabular-nums ${
                      rose ? "border-amber-400" : "border-slate-300"
                    }`}
                    value={line.total_cost}
                    onChange={(e) => updateLine(i, { total_cost: e.target.value })}
                    aria-label="Line total"
                  />
                  <button
                    onClick={() => removeLine(i)}
                    aria-label="Remove line"
                    title="Remove line"
                    className="shrink-0 text-base leading-none text-red-600 hover:text-red-700"
                  >
                    ✕
                  </button>
                </div>

                <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 pl-5 text-[11px]">
                  <span className="font-semibold tabular-nums text-slate-900">
                    {unitPriceLabel(unitPrice, material?.unit ?? "each")}
                  </span>
                  <span className="text-slate-400">
                    {ref
                      ? `last paid ${unitPriceLabel(Number(ref.unit_cost), material?.unit ?? "each")} · #${
                          ref.purchase_ref ?? ref.purchase_id
                        } · ${formatDayMonth(ref.at)}${ref.same_supplier ? "" : ` · ${ref.supplier_name ?? "other supplier"}`}`
                      : "never bought before — no price to compare"}
                  </span>
                  {deltaPct != null && Math.abs(deltaPct) >= 0.5 && (
                    <Badge
                      className={
                        deltaPct > 0
                          ? "bg-amber-100 text-amber-800"
                          : "bg-emerald-100 text-emerald-800"
                      }
                    >
                      {deltaPct > 0 ? "↑" : "↓"} {Math.abs(deltaPct).toFixed(1)}% vs last
                    </Badge>
                  )}
                  <span className="ml-auto flex items-center gap-1.5">
                    <span className={`font-semibold ${COVER_TEXT[tone]}`}>
                      {cover != null
                        ? `${cover.toFixed(1)} wk cover on arrival`
                        : "no usage history"}
                    </span>
                    {cover != null && expectedArrivalDate && (
                      <span className="text-slate-300">
                        · {displayQty(Math.round(onHandBy))} on hand by{" "}
                        {formatDayMonth(expectedArrivalDate)}
                      </span>
                    )}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <div className="flex items-center gap-2 border-t border-slate-100 px-3 py-2">
        <button
          onClick={addLine}
          className="rounded border border-dashed border-slate-300 px-2.5 py-1 text-[11.5px] font-semibold text-slate-600 hover:border-blue-500 hover:text-blue-600"
        >
          + Add line
        </button>
        <span className="text-[11px] text-slate-400">
          Key the line total from the invoice — the unit price is worked out from it
        </span>
      </div>
    </div>
  );
}
