import { useState } from "react";
import { GroupHeaderRow, Th } from "stocksmith-ui";

const ROWS = [
  ["Kraft mailer 200 x 150", "40 each"],
  ["Tissue paper - white", "310 each"],
];

/** Collapsible category groups in the Materials list. */
export const Collapsible = () => {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <table className="w-[560px] border-collapse overflow-hidden rounded-lg bg-white text-left text-[12.5px] shadow-sm">
      <thead>
        <tr className="border-b border-slate-200 bg-slate-50/60">
          <Th>Material</Th>
          <Th align="right">On hand</Th>
        </tr>
      </thead>
      <tbody>
        <GroupHeaderRow label="Packaging" count={ROWS.length} colSpan={2} collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)} />
        {!collapsed &&
          ROWS.map(([name, qty]) => (
            <tr key={name} className="border-b border-slate-100">
              <td className="p-2">{name}</td>
              <td className="p-2 text-right tabular-nums">{qty}</td>
            </tr>
          ))}
        <GroupHeaderRow label="Filament" count={1} colSpan={2} collapsed={false} onToggle={() => {}} />
        <tr>
          <td className="p-2">PLA - matte black</td>
          <td className="p-2 text-right tabular-nums">1,240 g</td>
        </tr>
      </tbody>
    </table>
  );
};

/** Static (no toggle); capitalize tidies a legacy lowercase category. */
export const Static = () => (
  <table className="w-[560px] border-collapse overflow-hidden rounded-lg bg-white text-left text-[12.5px] shadow-sm">
    <tbody>
      <GroupHeaderRow label="hardware" count={1} colSpan={2} capitalize />
      <tr>
        <td className="p-2">M3 x 8 mm screws</td>
        <td className="p-2 text-right tabular-nums">640 each</td>
      </tr>
    </tbody>
  </table>
);
