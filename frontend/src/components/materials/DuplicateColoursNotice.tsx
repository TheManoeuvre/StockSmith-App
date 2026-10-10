import { useMemo, useState } from "react";
import type { Material } from "../../api/types";
import { findDuplicateColours } from "../../lib/duplicateColours";
import { qtyWithUnit } from "../../lib/format";
import { MaterialMergeModal } from "./MaterialMergeModal";

/**
 * Flags active materials of one type that share a colour, with a way to merge them. The
 * variant colour picker can't tell two same-coloured spools apart by colour alone, and a
 * genuine duplicate is better combined than left to confuse it; the merge itself (stock,
 * weighted cost, every reference) is MaterialMergeModal's, with its own preview.
 *
 * Collapsed by default: two brands of the same colour is often deliberate, so this is a
 * nudge rather than something to dismiss before getting on with the page.
 */
export function DuplicateColoursNotice({ materials }: { materials: Material[] }) {
  const groups = useMemo(() => findDuplicateColours(materials), [materials]);
  const [open, setOpen] = useState(false);
  const [merging, setMerging] = useState<{ source: Material; targetId: number } | null>(null);

  if (groups.length === 0) return null;

  return (
    <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 text-left">
        <span aria-hidden>{open ? "▾" : "▸"}</span>
        <span>
          {groups.length === 1 ? "1 colour appears" : `${groups.length} colours appear`} on more than one material of
          the same type
        </span>
      </button>
      {open && (
        <div className="mt-2 flex flex-col gap-3">
          <p className="text-xs">
            If these are the same spool entered twice, merge them. If they are different brands you keep on purpose,
            leave them — the variant colour picker shows the full name for each.
          </p>
          {groups.map((group) => (
            <div key={`${group.typeName}-${group.colour}`}>
              <div className="font-medium">
                {group.colour}
                {group.typeName && <span className="font-normal text-amber-800"> · {group.typeName}</span>}
              </div>
              <ul className="mt-1 flex flex-col gap-1">
                {group.materials.map((m, i) => (
                  <li key={m.id} className="flex items-center gap-3">
                    <span className="min-w-0 flex-1 truncate">{m.name}</span>
                    <span className="text-xs text-amber-800">{qtyWithUnit(m.current_qty, m.unit)}</span>
                    {i > 0 ? (
                      <button
                        type="button"
                        onClick={() => setMerging({ source: m, targetId: group.materials[0].id })}
                        className="rounded border border-amber-300 bg-white px-2 py-0.5 text-xs hover:bg-amber-100"
                        aria-label={`Merge ${m.name} into ${group.materials[0].name}`}
                      >
                        Merge into first…
                      </button>
                    ) : (
                      <span className="w-[7.5rem]" />
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      {merging && (
        <MaterialMergeModal
          source={merging.source}
          initialTargetId={merging.targetId}
          onClose={() => setMerging(null)}
          onMerged={() => setMerging(null)}
        />
      )}
    </div>
  );
}
