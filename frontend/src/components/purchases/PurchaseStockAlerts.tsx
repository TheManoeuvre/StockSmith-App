import { Badge } from "../common/Badge";
import type { Material, PriceReference } from "../../api/types";
import type { PurchaseLineInput } from "../../api/purchases";
import { formatMoney } from "../../lib/money";
import { displayQty, isLowStock } from "../../lib/format";
import { suggestReorderQty } from "../../lib/reorder";

interface Alert {
  material: Material;
  label: string;
  badgeClass: string;
  suggestQty: number;
  lastUnit: number;
}

/**
 * Materials from this supplier (then anyone) that are running low and aren't already on a
 * line — each with a suggested quantity and one-click Add. Mirrors the reviewed design's
 * "Stock alerts" card: the chosen supplier's shortfalls first so one order clears a set.
 */
export function PurchaseStockAlerts({
  materials,
  supplierId,
  supplierName,
  lines,
  priceByMaterial,
  onAdd,
}: {
  materials: Material[];
  supplierId: number | null;
  supplierName: string;
  lines: PurchaseLineInput[];
  priceByMaterial: Map<number, PriceReference>;
  onAdd: (materialId: number, qty: string, totalCost: string) => void;
}) {
  const onALine = new Set(lines.map((l) => l.material_id));

  const alerts: Alert[] = materials
    .filter((m) => {
      if (onALine.has(m.id) || Number(m.reorder_threshold) <= 0) return false;
      // Below the reorder point, or forecast to run out inside the lead-time window.
      // "insufficient_data" on its own is not an alert — a well-stocked material with no
      // sales history yet isn't running low.
      return (
        m.stockout_status === "critical" ||
        m.stockout_status === "warning" ||
        isLowStock(m.current_qty, m.reorder_threshold)
      );
    })
    .sort((a, b) => {
      const aMine = a.default_supplier_id === supplierId ? 0 : 1;
      const bMine = b.default_supplier_id === supplierId ? 0 : 1;
      if (aMine !== bMine) return aMine - bMine;
      const bySupplier = (a.default_supplier_name ?? "").localeCompare(
        b.default_supplier_name ?? "",
      );
      if (bySupplier !== 0) return bySupplier;
      return (
        Number(a.weeks_of_supply ?? Infinity) - Number(b.weeks_of_supply ?? Infinity)
      );
    })
    .map((m) => {
      const low = isLowStock(m.current_qty, m.reorder_threshold);
      const critical = m.stockout_status === "critical";
      return {
        material: m,
        label: critical ? "Critical" : low ? "Below threshold" : "Running out",
        badgeClass: critical
          ? "bg-red-100 text-red-800"
          : low
            ? "bg-amber-100 text-amber-800"
            : "bg-sky-100 text-sky-800",
        suggestQty: suggestReorderQty(m),
        lastUnit: Number(
          priceByMaterial.get(m.id)?.unit_cost ?? m.avg_unit_cost ?? 0,
        ),
      };
    });

  return (
    <div className="overflow-hidden rounded border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2">
        <span className="text-[12px] font-semibold">Stock alerts</span>
        <span className="text-[11px] text-slate-400">
          {alerts.length
            ? `${alerts.length} below threshold or close to it${
                supplierName ? ` · ${supplierName} first, then by supplier` : ""
              }`
            : ""}
        </span>
      </div>

      {alerts.length === 0 ? (
        <p className="px-3 py-4 text-center text-[12.5px] text-slate-400">
          Nothing is running low.
        </p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {alerts.map(({ material: m, label, badgeClass, suggestQty, lastUnit }) => (
            <li key={m.id} className="flex items-center gap-2 px-3 py-2">
              <span
                className="h-3 w-3 shrink-0 rounded-sm border border-slate-200"
                style={{ backgroundColor: m.colour_hex ?? "#e2e8f0" }}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-[12.5px] font-medium">{m.name}</span>
                  <Badge className={badgeClass}>{label}</Badge>
                </div>
                <div className="text-[11px] text-slate-500">
                  {m.weeks_of_supply != null
                    ? `${Number(m.weeks_of_supply).toFixed(1)} wk to stockout`
                    : "no usage history"}{" "}
                  · {displayQty(m.current_qty)} of {displayQty(m.reorder_threshold)}
                  {m.default_supplier_name && m.default_supplier_id !== supplierId
                    ? ` · usually ${m.default_supplier_name}`
                    : ""}
                </div>
              </div>
              <div className="shrink-0 text-right">
                <div className="text-[11.5px] font-semibold tabular-nums">
                  +{displayQty(suggestQty)}
                  {m.unit === "each" ? "" : ` ${m.unit}`}
                </div>
                <div className="text-[11px] text-slate-400">
                  {formatMoney(String(suggestQty * lastUnit), "GBP")} at last price
                </div>
              </div>
              <button
                onClick={() =>
                  onAdd(
                    m.id,
                    String(suggestQty),
                    lastUnit > 0 ? (suggestQty * lastUnit).toFixed(2) : "",
                  )
                }
                className="shrink-0 rounded border border-slate-300 px-2.5 py-1 text-[11.5px] font-semibold hover:border-blue-500 hover:text-blue-600"
              >
                Add
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
