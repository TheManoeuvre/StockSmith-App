import { useMemo, useState } from "react";
import { Modal } from "../common/Modal";
import { ErrorBanner } from "../common/ErrorBanner";
import type { Material, Purchase, PurchaseLine } from "../../api/types";
import type { ReceiptLineInput } from "../../api/purchases";
import { normalizeQtyForUnit, roundQty, wholeNumberStepFor } from "../../lib/format";

/**
 * Records one delivery: what turned up, of which lines, on what date.
 *
 * Prefilled to "all of it", because that is what usually happens and typing the same number
 * back in is a tax on the common case. The cost box is left blank by default and shows the
 * pro-rata figure as its placeholder, so the sensible default is visible rather than
 * something you have to know about.
 */
function todayLocalISO(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

function proRata(line: PurchaseLine, qty: string): string {
  const ordered = Number(line.qty);
  const receiving = Number(qty);
  if (!ordered || !Number.isFinite(receiving)) return "";
  return ((Number(line.total_cost) * receiving) / ordered).toFixed(2);
}

export function ReceiveDialog({
  purchase,
  materials,
  busy,
  error,
  onSubmit,
  onClose,
}: {
  purchase: Purchase;
  materials: Material[];
  busy: boolean;
  error: unknown;
  onSubmit: (receivedAt: string, lines: ReceiptLineInput[]) => void;
  onClose: () => void;
}) {
  const open = useMemo(
    () => purchase.lines.filter((l) => Number(l.outstanding_qty) > 0),
    [purchase.lines],
  );
  const [receivedAt, setReceivedAt] = useState(todayLocalISO());
  const [qtys, setQtys] = useState<Record<number, string>>(() =>
    Object.fromEntries(open.map((l) => [l.id, roundQty(l.outstanding_qty)])),
  );
  const [costs, setCosts] = useState<Record<number, string>>({});

  const materialFor = (line: PurchaseLine) => materials.find((m) => m.id === line.material_id);
  const receiving = open.filter((l) => Number(qtys[l.id] ?? 0) > 0);
  const overReceipt = open.filter((l) => Number(qtys[l.id] ?? 0) > Number(l.outstanding_qty));
  // A draft purchase created from a low-stock alert has no cost on it yet, and receiving one
  // at zero silently drags the material's average cost down. Say so here rather than let it
  // be discovered later in a margin figure.
  const zeroCostLines = receiving.filter((l) => Number(l.total_cost) === 0 && !costs[l.id]);

  const receiveAll = () =>
    setQtys(Object.fromEntries(open.map((l) => [l.id, roundQty(l.outstanding_qty)])));

  const submit = () => {
    onSubmit(
      new Date(`${receivedAt}T12:00:00`).toISOString(),
      receiving.map((l) => ({
        line_id: l.id,
        qty: qtys[l.id],
        ...(costs[l.id] ? { total_cost: costs[l.id] } : {}),
      })),
    );
  };

  return (
    <Modal
      title="Record a delivery"
      maxWidth="max-w-4xl"
      onClose={onClose}
      footer={
        <div className="flex items-center justify-end gap-2">
          <button onClick={onClose} className="rounded border border-slate-300 px-4 py-2 text-sm" disabled={busy}>
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={busy || receiving.length === 0 || overReceipt.length > 0}
            className="rounded bg-slate-900 px-4 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Recording…" : `Record ${receiving.length} line${receiving.length === 1 ? "" : "s"}`}
          </button>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex items-end justify-between gap-4">
          <label className="flex flex-col gap-1">
            <span className="text-sm">Received on</span>
            <input
              type="date"
              className="rounded border border-slate-300 px-2 py-1"
              value={receivedAt}
              onChange={(e) => setReceivedAt(e.target.value)}
            />
          </label>
          <button onClick={receiveAll} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
            Receive everything outstanding
          </button>
        </div>

        {/* Wide table, narrow window: scroll inside its own box rather than shoving the page
            sideways. */}
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="p-2">Material</th>
                <th className="p-2">Ordered</th>
                <th className="p-2">Already in</th>
                <th className="p-2">Outstanding</th>
                <th className="p-2">Receiving now</th>
                <th className="p-2">Cost of this delivery (£)</th>
              </tr>
            </thead>
            <tbody>
              {open.map((line) => {
                const material = materialFor(line);
                const over = Number(qtys[line.id] ?? 0) > Number(line.outstanding_qty);
                return (
                  <tr key={line.id} className="border-b border-slate-100">
                    <td className="p-2">{material?.name ?? `Material #${line.material_id}`}</td>
                    <td className="p-2 text-slate-500">{roundQty(line.qty)}</td>
                    <td className="p-2 text-slate-500">{roundQty(line.received_qty)}</td>
                    <td className="p-2">{roundQty(line.outstanding_qty)}</td>
                    <td className="p-2">
                      <input
                        type="number"
                        min="0"
                        max={line.outstanding_qty}
                        step={wholeNumberStepFor(material?.unit)}
                        aria-label={`Receiving now for ${material?.name ?? line.material_id}`}
                        className={`w-24 rounded border px-2 py-1 ${over ? "border-red-400 bg-red-50" : "border-slate-300"}`}
                        value={qtys[line.id] ?? ""}
                        onChange={(e) => setQtys((prev) => ({ ...prev, [line.id]: e.target.value }))}
                        onBlur={(e) =>
                          setQtys((prev) => ({
                            ...prev,
                            [line.id]: normalizeQtyForUnit(e.target.value, material?.unit),
                          }))
                        }
                      />
                    </td>
                    <td className="p-2">
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        aria-label={`Cost of this delivery for ${material?.name ?? line.material_id}`}
                        placeholder={proRata(line, qtys[line.id] ?? "0")}
                        className="w-28 rounded border border-slate-300 px-2 py-1"
                        value={costs[line.id] ?? ""}
                        onChange={(e) => setCosts((prev) => ({ ...prev, [line.id]: e.target.value }))}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <p className="text-sm text-slate-500">
          Leave the cost blank and this delivery takes its share of the line total — the greyed
          figure. Fill it in only when the supplier billed this delivery for something else.
        </p>

        {zeroCostLines.length > 0 && (
          <p className="rounded bg-amber-50 p-2 text-sm text-amber-800">
            {zeroCostLines.length === 1 ? "One line has" : `${zeroCostLines.length} lines have`} no cost on the order,
            so receiving will book {zeroCostLines.length === 1 ? "it" : "them"} in at £0 and pull the material's average
            cost down. Add the cost to the order first, or enter it here.
          </p>
        )}

        {overReceipt.length > 0 && (
          <p className="rounded bg-red-50 p-2 text-sm text-red-700">
            More than was ordered is outstanding on{" "}
            {overReceipt.length === 1 ? "one line" : `${overReceipt.length} lines`}. Increase the order's quantity
            first if the supplier really sent extra.
          </p>
        )}

        <ErrorBanner error={error} />
      </div>
    </Modal>
  );
}
