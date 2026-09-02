import type { Material } from "../../api/types";
import { MaterialSelect } from "../materials/MaterialSelect";
import { formatMoney } from "../../lib/money";
import { normalizeQtyForUnit, wholeNumberStepFor } from "../../lib/format";

export interface BomLineTableRow {
  material_id: number;
  qty_required: string;
}

export interface BomLineCost {
  /** qty x the material's current average unit cost; null when either is unknown. */
  cost: number | null;
  /** This line's percentage of the table total; null when the total is zero or unknown. */
  share: number | null;
}

/**
 * Cost of each line and of the table as a whole, from the materials already in cache — the
 * BOM endpoints return only material_id and qty_required, so cost is a client-side join
 * against `["materials"]`, which every caller already fetches. No API call is involved.
 *
 * Exported separately from the component so the arithmetic is testable without rendering.
 *
 * The total sums RAW values and is rounded once for display. Rounding each line first and
 * summing those would drift by up to half a penny per line, and the visible rows would
 * provably fail to add up to the visible total.
 */
export function computeLineCosts(
  lines: BomLineTableRow[],
  materials: Material[] | undefined
): { perLine: BomLineCost[]; total: number | null } {
  const costs = lines.map((line) => {
    const material = materials?.find((m) => m.id === line.material_id);
    const qty = Number(line.qty_required);
    if (!material || !Number.isFinite(qty)) return null;
    const unitCost = Number(material.avg_unit_cost);
    if (!Number.isFinite(unitCost)) return null;
    return qty * unitCost;
  });

  const known = costs.filter((c): c is number => c !== null);
  const total = known.length > 0 ? known.reduce((sum, c) => sum + c, 0) : null;

  return {
    perLine: costs.map((cost) => ({
      cost,
      share: cost !== null && total !== null && total !== 0 ? (cost / total) * 100 : null,
    })),
    total,
  };
}

/**
 * Shared editable BOM table — one implementation behind the build BOM, the kitting BOM and
 * the default kitting BOM in Settings, which were three near-identical copies.
 *
 * `table-fixed` plus the explicit colgroup is what lets the build and kitting tables stack on
 * the Bill of Materials tab with their columns actually lining up. Left to content sizing
 * they only ever aligned by coincidence, and stopped as soon as one table held a longer
 * material name than the other.
 */
export function BomLineTable({
  lines,
  materials,
  filterText,
  onChangeLine,
  onRemoveLine,
  showMaxFromFreeStock = true,
  isDirty = false,
  tableClassName = "w-full table-fixed border-collapse bg-white text-left text-sm shadow-sm",
}: {
  lines: BomLineTableRow[];
  materials: Material[] | undefined;
  filterText?: string;
  onChangeLine: (index: number, patch: Partial<BomLineTableRow>) => void;
  onRemoveLine: (index: number) => void;
  /** Settings' default kitting BOM has no stock context, so it hides this column. */
  showMaxFromFreeStock?: boolean;
  /** Labels the total "(unsaved)" so a figure differing from the product header is explained. */
  isDirty?: boolean;
  tableClassName?: string;
}) {
  const { perLine, total } = computeLineCosts(lines, materials);
  const materialFor = (line: BomLineTableRow) => materials?.find((m) => m.id === line.material_id);

  // Free stock, not gross on-hand: material already reserved against an order can't also
  // cover this line. The product header's Build figure still counts gross (the server's
  // max_buildable), which is why the column says "free stock" rather than "theoretical".
  const maxFromFreeStock = (line: BomLineTableRow): number | null => {
    const material = materialFor(line);
    const qtyRequired = Number(line.qty_required);
    if (!material || !qtyRequired) return null;
    const free = Number(material.current_qty) - Number(material.allocated_qty);
    return Math.floor(free / qtyRequired);
  };

  const bottleneckIndex = (() => {
    const values = lines.map(maxFromFreeStock);
    const real = values.filter((v): v is number => v !== null);
    if (real.length < 2) return -1;
    return values.indexOf(Math.min(...real));
  })();

  return (
    <table className={tableClassName}>
      <colgroup>
        <col />
        <col className="w-16" />
        <col className="w-16" />
        {showMaxFromFreeStock && <col className="w-24" />}
        <col className="w-8" />
      </colgroup>
      <thead>
        <tr className="border-b border-slate-200">
          <th className="p-2">Material</th>
          <th className="p-2">Qty</th>
          <th className="p-2">Cost</th>
          {showMaxFromFreeStock && <th className="p-2">Cover (builds)</th>}
          <th className="p-2" />
        </tr>
      </thead>
      <tbody>
        {lines.map((line, i) => (
          <tr key={i} className="border-b border-slate-100">
            <td className="p-2">
              <div className="flex items-center gap-2">
                {materialFor(line)?.colour_hex && (
                  <span
                    className="h-4 w-4 shrink-0 rounded border border-slate-300"
                    style={{ backgroundColor: materialFor(line)!.colour_hex! }}
                  />
                )}
                <MaterialSelect
                  materials={materials ?? []}
                  value={line.material_id}
                  onChange={(material_id) => onChangeLine(i, { material_id })}
                  filterText={filterText}
                  showUnitCost
                  className="w-full rounded border border-slate-300 px-2 py-1"
                />
              </div>
            </td>
            <td className="p-2">
              <input
                className="w-24 rounded border border-slate-300 px-2 py-1"
                step={wholeNumberStepFor(materialFor(line)?.unit)}
                value={line.qty_required}
                onChange={(e) => onChangeLine(i, { qty_required: e.target.value })}
                onBlur={(e) =>
                  onChangeLine(i, {
                    qty_required: normalizeQtyForUnit(e.target.value, materialFor(line)?.unit),
                  })
                }
              />
            </td>
            <td className="p-2 tabular-nums">
              {perLine[i]?.cost != null ? formatMoney(String(perLine[i].cost), "GBP") : "—"}
            </td>
            {showMaxFromFreeStock &&
              (() => {
                const cover = maxFromFreeStock(line);
                const low = cover != null && cover < 5;
                return (
                  <td
                    className={`p-2 ${i === bottleneckIndex || low ? "font-semibold text-amber-700" : "text-slate-500"}`}
                  >
                    {cover ?? "—"}
                    {i === bottleneckIndex && (
                      <span className="ml-1 text-xs">(bottleneck)</span>
                    )}
                  </td>
                );
              })()}
            <td className="p-2">
              <button
                type="button"
                onClick={() => onRemoveLine(i)}
                aria-label="Remove line"
                className="text-red-600 hover:text-red-700"
              >
                ✕
              </button>
            </td>
          </tr>
        ))}
      </tbody>
      {lines.length > 0 && (
        <tfoot>
          <tr className="border-t border-slate-300 font-medium">
            <td className="p-2">{isDirty ? "Total (unsaved)" : "Total"}</td>
            <td className="p-2" />
            <td className="p-2 tabular-nums">
              {total != null ? formatMoney(String(total), "GBP") : "—"}
            </td>
            {showMaxFromFreeStock && <td className="p-2" />}
            <td className="p-2" />
          </tr>
        </tfoot>
      )}
    </table>
  );
}
