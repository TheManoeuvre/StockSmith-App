import { useId, useState } from "react";
import type { LocationRow } from "../../api/locations";
import { pathLabel, pathNames } from "../../lib/locations";
import { LocationTree } from "./LocationTree";

/**
 * A field that opens the location tree to choose from. `value` null means "Top level" where
 * that's allowed. `disabled` rows can't be chosen and say why — the move dialogs use it to stop
 * a location being moved into itself or its own contents.
 */
export function LocationPicker({
  rows,
  value,
  onChange,
  allowTopLevel = false,
  disabled,
  placeholder = "Choose a location",
  label,
}: {
  rows: LocationRow[];
  value: number | null;
  onChange: (id: number | null) => void;
  allowTopLevel?: boolean;
  disabled?: Map<number, string>;
  placeholder?: string;
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const panelId = useId();
  const chosen = value != null ? pathLabel(pathNames(rows, value)) : allowTopLevel ? "Top level" : "";

  return (
    <div className="relative">
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-2 rounded border border-slate-300 bg-white px-2 py-1.5 text-left text-sm"
      >
        <span className={chosen ? "truncate text-slate-900" : "text-slate-400"}>{chosen || placeholder}</span>
        <span aria-hidden="true" className="text-slate-400">
          ▾
        </span>
      </button>
      {open && (
        <div
          id={panelId}
          className="mt-1 flex max-h-64 flex-col gap-1 overflow-y-auto rounded-md border border-slate-200 bg-white p-1.5 shadow-sm"
        >
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search locations"
            aria-label="Search locations"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
          {allowTopLevel && (
            <button
              type="button"
              onClick={() => {
                onChange(null);
                setOpen(false);
              }}
              className={`rounded px-2 py-1 text-left text-[13px] hover:bg-slate-50 ${value == null ? "bg-slate-100 font-medium" : ""}`}
            >
              Top level
            </button>
          )}
          <LocationTree
            rows={rows}
            selectedId={value}
            query={query}
            disabled={disabled}
            defaultExpandAll
            ariaLabel={label}
            onSelect={(id) => {
              onChange(id);
              setOpen(false);
            }}
          />
        </div>
      )}
    </div>
  );
}
