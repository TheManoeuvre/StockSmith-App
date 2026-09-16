import { Th } from "stocksmith-ui";

/** The Materials list header row: sortable columns show a direction glyph. */
export const HeaderRow = () => (
  <table className="w-[640px] border-collapse overflow-hidden rounded-lg bg-white text-left text-[12.5px] shadow-sm">
    <thead>
      <tr className="border-b border-slate-200 bg-slate-50/60">
        <Th onClick={() => {}}>Material &#9650;</Th>
        <Th>Type</Th>
        <Th onClick={() => {}} align="right">On hand</Th>
        <Th onClick={() => {}} align="right">On order</Th>
        <Th onClick={() => {}} align="right">Unit cost</Th>
        <Th>Supplier</Th>
      </tr>
    </thead>
    <tbody>
      <tr className="border-b border-slate-100">
        <td className="p-2 font-medium">PLA - matte black</td>
        <td className="p-2 text-slate-500">Filament</td>
        <td className="p-2 text-right tabular-nums">1,240 g</td>
        <td className="p-2 text-right tabular-nums">2,000 g</td>
        <td className="p-2 text-right tabular-nums">£0.018</td>
        <td className="p-2 text-slate-500">Filamentive</td>
      </tr>
    </tbody>
  </table>
);
