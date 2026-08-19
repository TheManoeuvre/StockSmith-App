import { useState } from "react";
import { MaterialSelect } from "../materials/MaterialSelect";
import type { Material, PurchaseLine } from "../../api/types";
import type { PurchaseLineInput } from "../../api/purchases";
import { normalizeQtyForUnit, roundQty, wholeNumberStepFor } from "../../lib/format";

/**
 * The order's lines, editable.
 *
 * Two things changed when deliveries became a thing in their own right.
 *
 * Rows are keyed on the line's id rather than its array index. Index keys made React reuse
 * one row's inputs for another whenever a line was removed from the middle, which was
 * cosmetic before and is not now that a row carries a delivered quantity.
 *
 * Locking is per line, not per order. A part-received order has to stay editable on the
 * lines nothing has arrived for — that is the ordinary case, not an edge one. What a
 * received line will not allow is anything that contradicts what physically turned up:
 * changing its material, dropping it, or shrinking it below what came. The backend refuses
 * all three as well; this is so the answer is visible before the save, not after it.
 */
export function PurchaseLineEditor({
  materials,
  lines,
  saved,
  onChange,
  onCloseLine,
  onReopenLine,
}: {
  materials: Material[];
  lines: PurchaseLineInput[];
  /** The lines as the server has them — deliveries, closures and all. */
  saved?: PurchaseLine[];
  onChange: (lines: PurchaseLineInput[]) => void;
  onCloseLine?: (lineId: number) => void;
  onReopenLine?: (lineId: number) => void;
}) {
  const [filterText, setFilterText] = useState("");
  const showReceiving = (saved ?? []).some((l) => l.receipts.length > 0 || l.closed_at !== null);

  const savedFor = (line: PurchaseLineInput) =>
    line.id == null ? undefined : saved?.find((s) => s.id === line.id);

  const updateLine = (index: number, patch: Partial<PurchaseLineInput>) => {
    onChange(lines.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  };

  const removeLine = (index: number) => onChange(lines.filter((_, i) => i !== index));

  const addLine = () => {
    const firstUnused = materials.find((m) => !lines.some((l) => l.material_id === m.id)) ?? materials[0];
    if (!firstUnused) return;
    onChange([...lines, { material_id: firstUnused.id, qty: "0", total_cost: "0" }]);
  };

  return (
    <div className="flex flex-col gap-2">
      {materials.length > 8 && (
        <input
          className="w-64 rounded border border-slate-300 px-2 py-1 text-sm"
          placeholder="Filter materials…"
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
        />
      )}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="p-2">Material</th>
              <th className="p-2">Ordered</th>
              {showReceiving && <th className="p-2">Received</th>}
              {showReceiving && <th className="p-2">Outstanding</th>}
              <th className="p-2">Total cost (£)</th>
              <th className="p-2">Notes</th>
              <th className="p-2" />
            </tr>
          </thead>
          <tbody>
            {lines.map((line, i) => {
              const material = materials.find((m) => m.id === line.material_id);
              const persisted = savedFor(line);
              const delivered = persisted ? Number(persisted.received_qty) : 0;
              const locked = delivered > 0;
              const closed = persisted?.closed_at != null;
              return (
                <tr key={line.id ?? `new-${i}`} className="border-b border-slate-100">
                  <td className="p-2">
                    <MaterialSelect
                      materials={materials}
                      value={line.material_id}
                      onChange={(material_id) => updateLine(i, { material_id })}
                      filterText={filterText}
                      disabled={locked}
                    />
                  </td>
                  <td className="p-2">
                    <input
                      className="w-24 rounded border border-slate-300 px-2 py-1"
                      step={wholeNumberStepFor(material?.unit)}
                      min={locked ? persisted?.received_qty : undefined}
                      title={locked ? `${roundQty(persisted!.received_qty)} has already arrived on this line` : undefined}
                      value={line.qty}
                      onChange={(e) => updateLine(i, { qty: e.target.value })}
                      onBlur={(e) => updateLine(i, { qty: normalizeQtyForUnit(e.target.value, material?.unit) })}
                    />
                  </td>
                  {showReceiving && (
                    <td className="p-2 text-slate-500">{persisted ? roundQty(persisted.received_qty) : "—"}</td>
                  )}
                  {showReceiving && (
                    <td className="p-2">
                      {persisted ? roundQty(persisted.outstanding_qty) : "—"}
                      {closed && (
                        <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">
                          closed short
                        </span>
                      )}
                    </td>
                  )}
                  <td className="p-2">
                    <input
                      className="w-24 rounded border border-slate-300 px-2 py-1"
                      value={line.total_cost}
                      onChange={(e) => updateLine(i, { total_cost: e.target.value })}
                    />
                  </td>
                  <td className="p-2">
                    <input
                      className="rounded border border-slate-300 px-2 py-1"
                      value={line.notes ?? ""}
                      onChange={(e) => updateLine(i, { notes: e.target.value })}
                    />
                  </td>
                  <td className="p-2 whitespace-nowrap">
                    {locked ? (
                      closed ? (
                        onReopenLine && (
                          <button onClick={() => onReopenLine(line.id!)} className="text-xs text-slate-600 underline">
                            Reopen
                          </button>
                        )
                      ) : (
                        persisted &&
                        Number(persisted.outstanding_qty) > 0 &&
                        onCloseLine && (
                          <button
                            onClick={() => onCloseLine(line.id!)}
                            title="The rest of this line is never coming"
                            className="text-xs text-slate-600 underline"
                          >
                            Close short
                          </button>
                        )
                      )
                    ) : (
                      <button onClick={() => removeLine(i)} className="text-red-600">
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <button onClick={addLine} className="w-fit rounded border border-slate-300 px-3 py-1.5 text-sm">
        + Add line
      </button>
    </div>
  );
}
