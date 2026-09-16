import { FieldRow } from "stocksmith-ui";

/** A stacked detail form (Materials > Details tab). */
export const StackedForm = () => (
  <div className="flex w-[480px] flex-col gap-3">
    <FieldRow label="Name">
      <input defaultValue="PLA filament - matte black" className="w-full rounded border border-slate-300 px-2 py-1 text-sm" />
    </FieldRow>
    <FieldRow label="Category">
      <select defaultValue="Filament" className="rounded border border-slate-300 px-2 py-1 text-sm">
        <option>Filament</option>
        <option>Packaging</option>
        <option>Hardware</option>
      </select>
    </FieldRow>
    <FieldRow label="Reorder at">
      <div className="flex items-center gap-2">
        <input type="number" defaultValue={500} className="w-28 rounded border border-slate-300 px-2 py-1 text-sm" />
        <span className="text-xs text-slate-400">g</span>
      </div>
    </FieldRow>
    <FieldRow label="Last counted">
      <p className="text-sm text-slate-600">04 Sep</p>
    </FieldRow>
  </div>
);

/** align="right" pushes the control to the row's edge (Pricing rows). */
export const RightAligned = () => (
  <div className="flex w-[480px] flex-col gap-3">
    <FieldRow label="Sale price" align="right">
      <input defaultValue="14.00" className="w-28 rounded border border-slate-300 px-2 py-1 text-right text-sm" />
    </FieldRow>
    <FieldRow label="Cost of goods" align="right">
      <p className="text-sm text-slate-600">£4.12</p>
    </FieldRow>
  </div>
);
