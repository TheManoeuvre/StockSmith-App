/**
 * A boolean toggle styled as a track+knob rather than a bare checkbox. Still an
 * `<input type="checkbox">` under the hood — no `role` override — so `getByRole("checkbox")`
 * queries in existing tests keep working unchanged.
 */
export function Switch({
  id,
  checked,
  onChange,
  disabled,
  ariaLabel,
  className = "",
}: {
  id?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  ariaLabel?: string;
  className?: string;
}) {
  return (
    <span className={`relative inline-flex h-5 w-9 shrink-0 items-center ${className}`}>
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label={ariaLabel}
        onChange={(e) => onChange(e.target.checked)}
        className="peer absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
      />
      <span
        aria-hidden="true"
        className="pointer-events-none h-5 w-9 rounded-full bg-slate-300 transition-colors peer-checked:bg-slate-900 peer-disabled:opacity-50"
      />
      <span
        aria-hidden="true"
        className="pointer-events-none absolute left-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform peer-checked:translate-x-4"
      />
    </span>
  );
}
