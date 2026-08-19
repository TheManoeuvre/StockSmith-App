import type { PurchaseStatus } from "../../api/types";

/**
 * The order's status, in one place.
 *
 * It was a two-way ternary copied onto three pages, which was fine while there were two
 * states. There are three now, and the failure mode of the copies was not a crash — it was
 * a part-delivered order rendering as "Ordered" on one screen and "Received" on another.
 */
const STYLES: Record<PurchaseStatus, { label: string; className: string }> = {
  ordered: { label: "Ordered", className: "bg-amber-100 text-amber-800" },
  partially_received: { label: "Part received", className: "bg-sky-100 text-sky-800" },
  received: { label: "Received", className: "bg-green-100 text-green-800" },
};

export function PurchaseStatusPill({ status }: { status: PurchaseStatus }) {
  const { label, className } = STYLES[status];
  return <span className={`rounded px-2 py-0.5 text-xs ${className}`}>{label}</span>;
}
