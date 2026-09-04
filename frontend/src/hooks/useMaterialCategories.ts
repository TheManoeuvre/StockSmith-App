import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { materialCategoriesApi } from "../api/materialCategories";
import type { MaterialCategory } from "../api/types";

/**
 * The material categories, already in display order, plus lookups by name and id.
 *
 * One hook rather than a useQuery in each consumer, because this replaces four hardcoded
 * constants that had drifted apart: two copies of a CATEGORIES array (materials/index.tsx and
 * materials/$materialId.tsx), a CATEGORY_LABELS map in MaterialSelect, and an
 * AUTO_EACH_CATEGORIES list. Their being separate is exactly why the BOM picker grouped
 * categories in a different order from the materials table.
 *
 * Which key to look up by depends on what you're holding. A Material carries `category` (the
 * name) and `category_id`; prefer the id where the question is "are these two the same
 * category", since names are user-editable, and the name where you only have a string.
 *
 * The query key matches the one settings.tsx passes to ReferenceDataTable, which is the whole
 * cache-coherence mechanism — editing a category there invalidates it for everything here.
 */
export function useMaterialCategories() {
  const { data } = useQuery({ queryKey: ["material-categories"], queryFn: materialCategoriesApi.list });
  const categories: MaterialCategory[] = useMemo(() => data ?? [], [data]);

  return useMemo(
    () => ({
      categories,
      byName: new Map(categories.map((c) => [c.name, c])),
      byId: new Map(categories.map((c) => [c.id, c])),
      /** Names of the categories flagged "Show in Kitting BOM list" — the set the kitting-BOM
       *  pickers restrict to, and what a new kitting line should default into rather than
       *  landing on filament or hardware. */
      kittingBomCategoryNames: new Set(
        categories.filter((c) => c.show_in_kitting_bom_list).map((c) => c.name)
      ),
    }),
    [categories]
  );
}
