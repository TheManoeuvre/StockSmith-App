import type { ReactNode } from "react";

/**
 * The one card shape every settings section sits in. Replaces the hand-rolled
 * `<div className="… rounded border …">` + `<h2>`/`<p>` each form grew its own copy of, so
 * the six settings tabs read as one surface. A `<section>` (not a `<div>`) so tests can scope
 * to a card with `heading.closest("section")`.
 */
export function SettingsCard({
  title,
  help,
  action,
  children,
}: {
  title: string;
  help?: ReactNode;
  /** Rendered top-right — a Save button, a toggle, a link out. */
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3 rounded border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-medium">{title}</h2>
          {help && <p className="text-sm text-slate-500">{help}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
