import type { ReactNode } from "react";
import type { Delta } from "../../lib/insights";

/** A pill that is either on or off: the channel and "failed builds" toggles. */
export function ToggleChip({
  pressed,
  onClick,
  children,
  title,
  disabled,
}: {
  pressed: boolean;
  onClick: () => void;
  children: ReactNode;
  title?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`rounded-md border px-2.5 py-1 text-xs font-medium ${
        pressed
          ? "border-slate-900 bg-slate-900 text-white"
          : "border-slate-300 bg-white text-slate-600 hover:bg-slate-100"
      }`}
    >
      {pressed ? "✓ " : ""}
      {children}
    </button>
  );
}

const DELTA_TONE: Record<Delta["tone"], string> = {
  good: "text-green-700",
  bad: "text-red-700",
  flat: "text-slate-500",
};

/**
 * A headline figure with its change against the comparison period. Same shape as
 * common/Stat (white tile, small label over a large value) but the sub line can carry a
 * coloured delta, which Stat's string-only `sub` can't.
 */
export function InsightsStat({
  label,
  value,
  delta,
  deltaNote,
  hint,
  title,
  note,
}: {
  label: string;
  value: string;
  delta?: Delta | null;
  /** What the delta is against, e.g. "vs 2025". */
  deltaNote?: string;
  /** The formula, shown on the "?" . */
  hint?: string;
  /** Hover text for the whole tile. */
  title?: string;
  note?: ReactNode;
}) {
  return (
    <div className="rounded p-3 shadow-sm bg-white" title={title}>
      <p className="flex items-center gap-1 text-xs text-slate-500">
        {label}
        {hint && (
          <span
            title={hint}
            aria-label={hint}
            role="img"
            className="inline-flex h-[13px] w-[13px] cursor-help items-center justify-center rounded-full border border-slate-300 text-[9px] text-slate-500"
          >
            ?
          </span>
        )}
      </p>
      <p className="text-lg font-semibold tabular-nums">{value}</p>
      {delta && (
        <p className="mt-0.5 text-[11px] text-slate-400 tabular-nums">
          <span className={`font-semibold ${DELTA_TONE[delta.tone]}`}>{delta.text}</span>
          {deltaNote ? ` ${deltaNote}` : ""}
        </p>
      )}
      {note && <p className="mt-0.5 text-[11px] text-slate-400">{note}</p>}
    </div>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-lg bg-white shadow-sm ${className}`}>{children}</div>;
}

export function SectionHead({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-2 mt-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="text-sm font-semibold">{title}</h2>
        {sub && <p className="text-xs text-slate-500">{sub}</p>}
      </div>
      {actions}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-slate-200 ${className}`} aria-hidden="true" />;
}

export function TilesSkeleton() {
  return (
    <div role="status" aria-label="Loading" className="mt-4 grid grid-cols-2 gap-2.5 xl:grid-cols-4">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="rounded bg-white p-3 shadow-sm">
          <Skeleton className="h-3 w-1/2" />
          <Skeleton className="mt-2 h-5 w-2/3" />
          <Skeleton className="mt-2 h-3 w-1/3" />
        </div>
      ))}
    </div>
  );
}

export function PendingBadge() {
  return <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-800">pending</span>;
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <Card className="mt-4 px-6 py-12 text-center">
      <p className="text-[15px] font-semibold">{title}</p>
      {children && <p className="mx-auto mt-1 max-w-md text-xs text-slate-500">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </Card>
  );
}
