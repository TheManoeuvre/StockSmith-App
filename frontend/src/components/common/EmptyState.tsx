import type { ReactNode } from "react";

/**
 * What a page shows when there's nothing in it yet. Says what the page is for and what to do next,
 * rather than only that it's empty — docs/plan-onboarding.md §3. Only for "nothing exists yet";
 * a search or filter that matches nothing keeps its own short message.
 */
export function EmptyState({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-lg border border-dashed border-slate-300 bg-white p-6">
      <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
      <div className="max-w-2xl text-sm text-slate-600">{children}</div>
      {actions && <div className="mt-1 flex flex-wrap items-center gap-4 text-sm">{actions}</div>}
    </div>
  );
}
