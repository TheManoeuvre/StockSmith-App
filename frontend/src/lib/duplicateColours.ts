import type { Material } from "../api/types";

export interface DuplicateColourGroup {
  colour: string;
  typeName: string | null;
  /** Oldest first, so the first is the natural one to keep. */
  materials: Material[];
}

/**
 * Active materials of one type that share a colour name — a second brand bought because the
 * first ran out, or one spool imported under two spellings. Compared case-insensitively and
 * ignoring surrounding space. Materials with no type or no colour are never grouped: without
 * both there is nothing to say two of them are the same thing.
 */
export function findDuplicateColours(materials: Material[]): DuplicateColourGroup[] {
  const groups = new Map<string, Material[]>();
  for (const m of materials) {
    const colour = m.colour?.trim();
    if (!m.is_active || m.material_type_id == null || !colour) continue;
    const key = `${m.material_type_id}\u0000${colour.toLowerCase()}`;
    groups.set(key, [...(groups.get(key) ?? []), m]);
  }
  return [...groups.values()]
    .filter((list) => list.length > 1)
    .map((list) => {
      const sorted = [...list].sort((a, b) => a.id - b.id);
      return { colour: sorted[0].colour!.trim(), typeName: sorted[0].material_type_name, materials: sorted };
    })
    .sort((a, b) => (a.typeName ?? "").localeCompare(b.typeName ?? "") || a.colour.localeCompare(b.colour));
}
