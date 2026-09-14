export interface FilterTabDef {
  id: string;
  label: string;
  /** Shown as a muted count after the label. Omit for a tab that has no meaningful count. */
  count?: number;
}

/**
 * The filter strip that sits above a list table — one tab per saved view, each with its row
 * count, active tab underlined. Distinct from `Tabs.tsx` (which switches panes inside the
 * detail panel and carries no counts): this one filters the list under it, and every list
 * screen's design uses the same strip, so it's shared from the start.
 */
export function FilterTabs({
  tabs,
  active,
  onChange,
}: {
  tabs: FilterTabDef[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="flex gap-1 border-b border-slate-200">
      {tabs.map((tab) => {
        const isActive = tab.id === active;
        return (
          <button
            key={tab.id}
            onClick={() => onChange(tab.id)}
            className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-[12.5px] font-medium ${
              isActive
                ? "border-blue-600 text-slate-900"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            {tab.label}
            {tab.count != null && (
              <span
                className={`rounded px-1.5 py-0.5 text-[10.5px] font-semibold tabular-nums ${
                  isActive ? "bg-blue-100 text-blue-700" : "bg-slate-100 text-slate-500"
                }`}
              >
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
