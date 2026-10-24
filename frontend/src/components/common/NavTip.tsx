/**
 * Hover/focus tooltip for a sidebar item. Only shown while the sidebar is collapsed to icons
 * (below the `lg` breakpoint) — when the labels are visible there is nothing to add.
 *
 * Render as a sibling of the link/button inside a `group relative` wrapper, so the tooltip text stays out of the control's own text and accessible name. Pure CSS, so it follows the breakpoint with no JS.
 */
export function NavTip({
  label,
  hint,
  badge,
}: {
  label: string;
  hint?: string;
  badge?: number;
}) {
  return (
    <span
      aria-hidden="true"
      className="pointer-events-none absolute left-full top-1/2 z-40 ml-2 hidden w-max max-w-[220px] -translate-y-1/2 rounded-md bg-slate-900 px-2.5 py-1.5 text-left shadow-lg group-hover:max-lg:block group-focus-within:max-lg:block"
    >
      <span className="block text-[12px] font-semibold text-white">
        {label}
        {!!badge && (
          <span className="ml-1.5 font-normal text-slate-300">· {badge}</span>
        )}
      </span>
      {hint && (
        <span className="mt-0.5 block text-[11px] font-normal leading-snug text-slate-300">
          {hint}
        </span>
      )}
    </span>
  );
}
