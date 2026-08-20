import { useMemo } from "react";
import type { Material, Purchase } from "../../api/types";
import { roundQty } from "../../lib/format";

/**
 * Every delivery recorded against this order, newest first, each undoable on its own.
 *
 * Grouped by batch rather than by line, because that is the unit a person remembers: "the
 * van on Tuesday", not "the third receipt against line two". Undoing one delivery is the
 * thing someone actually wants when a single figure was keyed wrong — reversing the whole
 * order to fix it is what stops people using the feature at all.
 */
interface Batch {
  key: string;
  batchId: string | null;
  receivedAt: string;
  lines: { materialName: string; qty: string; cost: string | null; receiptId: number }[];
}

export function ReceiptHistoryPanel({
  purchase,
  materials,
  busy,
  onUndoBatch,
  onUndoReceipt,
}: {
  purchase: Purchase;
  materials: Material[];
  busy: boolean;
  onUndoBatch: (batchId: string) => void;
  onUndoReceipt: (receiptId: number) => void;
}) {
  const batches = useMemo<Batch[]>(() => {
    const byKey = new Map<string, Batch>();
    for (const line of purchase.lines) {
      const materialName = materials.find((m) => m.id === line.material_id)?.name ?? `Material #${line.material_id}`;
      for (const receipt of line.receipts) {
        // A receipt predating batch ids (or written by the migration) stands on its own
        // rather than being lumped in with unrelated ones that share a date.
        const key = receipt.batch_id ?? `receipt-${receipt.id}`;
        const batch = byKey.get(key) ?? {
          key,
          batchId: receipt.batch_id,
          receivedAt: receipt.received_at,
          lines: [],
        };
        batch.lines.push({
          materialName,
          qty: receipt.qty,
          cost: receipt.total_cost,
          receiptId: receipt.id,
        });
        byKey.set(key, batch);
      }
    }
    return [...byKey.values()].sort((a, b) => b.receivedAt.localeCompare(a.receivedAt));
  }, [purchase.lines, materials]);

  if (batches.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold">Deliveries</h2>
      <div className="overflow-x-auto rounded bg-white shadow-sm">
        <table className="w-full border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="p-2">Received</th>
              <th className="p-2">What arrived</th>
              <th className="p-2" />
            </tr>
          </thead>
          <tbody>
            {batches.map((batch) => (
              <tr key={batch.key} className="border-b border-slate-100 align-top">
                <td className="p-2 whitespace-nowrap">{batch.receivedAt.slice(0, 10)}</td>
                <td className="p-2">
                  <ul className="flex flex-col gap-0.5">
                    {batch.lines.map((line) => (
                      <li key={line.receiptId}>
                        {roundQty(line.qty)} × {line.materialName}
                        {line.cost !== null && (
                          <span className="ml-2 text-xs text-slate-500">billed £{Number(line.cost).toFixed(2)}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </td>
                <td className="p-2 text-right">
                  <button
                    disabled={busy}
                    onClick={() =>
                      batch.batchId ? onUndoBatch(batch.batchId) : onUndoReceipt(batch.lines[0].receiptId)
                    }
                    className="rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-50"
                  >
                    Undo
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
