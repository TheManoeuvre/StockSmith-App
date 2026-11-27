import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { workApi, type WorkCard, type WorkEvent, type WorkOrderCard } from "../../api/work";
import { formatDayMonth } from "../../lib/format";
import { ErrorBanner } from "../common/ErrorBanner";

export function DueBadge({ due, late }: { due: string | null; late: boolean }) {
  if (!due) return <span className="text-[11.5px] text-slate-400">No date</span>;
  return (
    <span className={`rounded px-1.5 py-0.5 text-[11.5px] font-medium ${late ? "bg-red-50 text-red-700" : "bg-slate-100 text-slate-600"}`}>
      {late ? "Late · " : "Due "}
      {formatDayMonth(due)}
    </span>
  );
}

function orderList(ids: number[]): string {
  if (ids.length === 0) return "";
  const shown = ids.slice(0, 4).map((id) => `#${id}`).join(", ");
  return ids.length > 4 ? `${shown} +${ids.length - 4} more` : shown;
}

/** One task: a stage of work for a product (or a mixed batch of designs). */
export function TaskCard({ card, next, onRecorded }: { card: WorkCard; next: boolean; onRecorded: (events: WorkEvent[]) => void }) {
  const queryClient = useQueryClient();
  const [qty, setQty] = useState(String(card.qty));
  const mixed = card.parts.length > 1;
  const record = useMutation({
    mutationFn: () =>
      workApi.recordProgress({
        product_id: card.product_id,
        // One variant: say which. Several: let the server spread the number, most urgent first.
        variant_id: mixed ? null : (card.parts[0]?.variant_id ?? null),
        stage_id: card.stage_id,
        qty: Number(qty),
        order_line_id: card.order_line_id,
      }),
    onSuccess: (events) => {
      onRecorded(events);
      queryClient.invalidateQueries({ queryKey: ["work"] });
    },
  });
  const unit = card.unit_label ? card.unit_label.toLowerCase() : "";

  return (
    <li className={`flex flex-col gap-2 rounded-lg border bg-white p-3 ${next ? "border-slate-900" : "border-slate-200"}`} aria-label={`${card.stage_name}: ${card.product_name}`}>
      <div className="flex flex-wrap items-center gap-2">
        {next && <span className="rounded bg-slate-900 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-white">Next</span>}
        <span className="font-medium">{card.stage_name}</span>
        <span className="text-slate-400">·</span>
        <span>{card.product_name}</span>
        <span className="flex-1" />
        <DueBadge due={card.due} late={card.late} />
      </div>

      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
        <span className="text-lg font-semibold tabular-nums">
          {card.qty}
          {unit ? <span className="ml-1 text-sm font-normal text-slate-500">{unit}</span> : null}
        </span>
        {card.parts.some((p) => p.variant_name) && (
          <span className="text-slate-600">
            {card.parts
              .filter((p) => p.variant_name)
              .map((p) => `${p.qty} × ${p.variant_name}`)
              .join(", ")}
          </span>
        )}
        {card.batch_note && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[11.5px] text-amber-800">{card.batch_note}</span>}
        {card.order_ids.length > 0 && <span className="text-[12px] text-slate-500">for {orderList(card.order_ids)}</span>}
      </div>

      {card.can_record ? (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (Number(qty) > 0) record.mutate();
          }}
        >
          <label className="flex items-center gap-2 text-sm text-slate-600">
            Done
            <input
              aria-label={`How many ${card.stage_name} done`}
              className="w-20 rounded border border-slate-300 px-2 py-1 text-right text-sm tabular-nums"
              inputMode="numeric"
              value={qty}
              onChange={(e) => setQty(e.target.value.replace(/\D/g, ""))}
            />
          </label>
          <button type="submit" disabled={!(Number(qty) > 0) || record.isPending} className="rounded bg-slate-900 px-3 py-1 text-sm text-white disabled:opacity-50">
            Done
          </button>
        </form>
      ) : (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          {card.parts.map((part) => (
            <Link
              key={part.variant_id ?? "base"}
              to="/products/$productId"
              params={{ productId: String(card.product_id) }}
              search={{ tab: "stock", ...(part.variant_id ? { variantId: part.variant_id } : {}) }}
              className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-50"
            >
              Record build{part.variant_name ? ` — ${part.variant_name}` : ""}
            </Link>
          ))}
        </div>
      )}
      <ErrorBanner error={record.error} />
    </li>
  );
}

/** An order: ready to pack now, or waiting on something. */
export function OrderCard({ card }: { card: WorkOrderCard }) {
  return (
    <li className="flex flex-col gap-1.5 rounded-lg border border-slate-200 bg-white p-3" aria-label={`Order ${card.order_id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Link to="/orders/$orderId" params={{ orderId: String(card.order_id) }} className="font-medium underline decoration-slate-300 underline-offset-2">
          Order #{card.order_id}
        </Link>
        {card.buyer_name && <span className="text-slate-600">{card.buyer_name}</span>}
        <span className="text-[12px] text-slate-500">
          {card.ready_lines} of {card.total_lines} {card.total_lines === 1 ? "item" : "items"} ready
        </span>
        <span className="flex-1" />
        <DueBadge due={card.due} late={card.late} />
      </div>
      <ul className="text-sm text-slate-700">
        {card.lines.map((line, i) => (
          <li key={i} className={line.ready ? "" : "text-slate-400"}>
            {line.qty} × {line.product_name}
            {line.variant_name ? ` — ${line.variant_name}` : ""}
          </li>
        ))}
      </ul>
      {card.waiting_for.length > 0 && <p className="text-[12px] text-amber-800">Waiting for: {card.waiting_for.join("; ")}</p>}
    </li>
  );
}
