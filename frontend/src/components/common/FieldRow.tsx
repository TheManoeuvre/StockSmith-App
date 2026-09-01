import type { ReactNode } from "react";

/**
 * One label-left, content-right row in a vertically stacked detail form (Materials'
 * Details/Counting/Supplier tabs). Extracted once three tabs needed the same "stacked
 * rows" layout the reviewed design uses, in place of the old flex-wrap label-above-input
 * fields.
 */
export function FieldRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-36 shrink-0 text-sm text-slate-600">{label}</span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
