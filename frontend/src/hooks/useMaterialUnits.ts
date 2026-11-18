import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { materialUnitsApi } from "../api/materialUnits";
import type { MaterialUnitRow } from "../api/types";
import { setUnitRules } from "../lib/format";

/**
 * The units of measure, already in display order, plus a lookup by name.
 *
 * This replaces the `UNITS = ["g", "ml", "each"]` arrays that were copied into the materials
 * list and detail pages. It also feeds the formatting helpers in lib/format.ts: those are plain
 * functions called from dozens of render paths that hold only a unit *name*, so the rules
 * (decimal places, whether the name is shown) are pushed into a module-level table here rather
 * than threaded through every call site. The root layout calls this once, so the table is
 * populated for the whole app; editing a unit in Settings invalidates the same query key and
 * refreshes it.
 *
 * The query key matches the one ListsMasterDetail passes to ReferenceDataTable, which is the
 * whole cache-coherence mechanism.
 */
export function useMaterialUnits() {
  const { data } = useQuery({ queryKey: ["material-units"], queryFn: materialUnitsApi.list });
  if (data) setUnitRules(data);
  const units: MaterialUnitRow[] = useMemo(() => data ?? [], [data]);

  return useMemo(() => ({ units, byName: new Map(units.map((u) => [u.name, u])) }), [units]);
}
