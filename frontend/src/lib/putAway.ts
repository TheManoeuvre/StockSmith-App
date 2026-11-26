import type { PutAwayItem } from "../components/locations/PutAwayModal";

/** One put-away question per material from the lines of a delivery, adding up repeats. */
export function putAwayItemsFor(
  arrived: { materialId: number | undefined; qty: number }[],
  materials: { id: number; name: string }[] | undefined,
): PutAwayItem[] {
  const totals = new Map<number, number>();
  for (const { materialId, qty } of arrived) {
    if (materialId == null || !(qty > 0)) continue;
    totals.set(materialId, (totals.get(materialId) ?? 0) + qty);
  }
  return [...totals].map(([materialId, qty]) => ({
    ref: { material_id: materialId },
    name: materials?.find((m) => m.id === materialId)?.name ?? "Material",
    qty,
  }));
}
