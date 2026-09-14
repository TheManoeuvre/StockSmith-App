/**
 * One headline-figure tile in a detail panel's persistent stat row (Materials, Products).
 * `sub` is the small line under the value; `tone="highlight"` tints the tile; `valueClassName`
 * colours the value (e.g. red when a sellable figure is zero).
 */
export function Stat({
  label,
  value,
  sub,
  tone = "default",
  valueClassName,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "default" | "highlight";
  valueClassName?: string;
}) {
  return (
    <div
      className={`rounded p-3 shadow-sm ${tone === "highlight" ? "bg-blue-50" : "bg-white"}`}
    >
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`text-lg font-semibold ${valueClassName ?? ""}`}>{value}</p>
      {sub && <p className="mt-0.5 text-[11px] text-slate-400">{sub}</p>}
    </div>
  );
}
