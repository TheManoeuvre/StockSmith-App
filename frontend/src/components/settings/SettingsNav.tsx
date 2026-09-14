export interface SettingsNavItem {
  id: string;
  label: string;
}

export interface SettingsNavGroup {
  label: string;
  items: SettingsNavItem[];
}

/**
 * The grouped sub-nav that replaced Settings' old flat row of six tabs — three groups (what
 * you sell through, what you stock, and the app itself) instead of one undifferentiated strip.
 */
export function SettingsNav({
  groups,
  active,
  onChange,
}: {
  groups: SettingsNavGroup[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <nav className="flex w-[198px] shrink-0 flex-col gap-4">
      {groups.map((group) => (
        <div key={group.label} className="flex flex-col gap-0.5">
          <p className="px-2.5 pb-1 text-[10.5px] font-semibold uppercase tracking-[.06em] text-slate-400">
            {group.label}
          </p>
          {group.items.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onChange(item.id)}
              className={`rounded-md px-2.5 py-1.5 text-left text-[12.5px] font-medium ${
                active === item.id ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-50"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      ))}
    </nav>
  );
}
