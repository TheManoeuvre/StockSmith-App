import type { ReactNode } from "react";

/**
 * A small colour-coded status pill.
 *
 * Every status/channel badge in the app (order status, purchase status, platform badges) was
 * a copy-pasted `<span className="rounded px-2 py-0.5 text-xs ...">` — same shape, colours
 * decided per call site. This standardises the shape only; callers still own which colour
 * means what for their own domain.
 */
export function Badge({ className, children }: { className: string; children: ReactNode }) {
  return <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${className}`}>{children}</span>;
}
