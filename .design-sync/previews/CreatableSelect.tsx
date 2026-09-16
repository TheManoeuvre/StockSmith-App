import { useState } from "react";
import { CreatableSelect } from "stocksmith-ui";

const SUPPLIERS = [
  { id: 1, name: "Filamentive" },
  { id: 2, name: "Prusa Research" },
  { id: 3, name: "RS Components" },
];

/** Supplier field: an existing name resolves to its id; a new name stays unresolved. */
export const Supplier = () => {
  const [value, setValue] = useState("Filamentive");
  const [resolved, setResolved] = useState<number | null>(1);
  return (
    <div className="flex w-80 flex-col gap-2">
      <CreatableSelect
        options={SUPPLIERS}
        value={value}
        onChange={setValue}
        onResolved={setResolved}
        placeholder="Supplier"
        className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
      />
      <p className="text-xs text-slate-500">{resolved ? `Existing supplier #${resolved}` : "Will be created on save"}</p>
    </div>
  );
};

export const Empty = () => {
  const [value, setValue] = useState("");
  return (
    <div className="w-80">
      <CreatableSelect
        options={SUPPLIERS}
        value={value}
        onChange={setValue}
        onResolved={() => {}}
        placeholder="Type or pick a supplier"
        className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
      />
    </div>
  );
};
