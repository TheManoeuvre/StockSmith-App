import type { Material, MaterialCategory } from "../api/types";

/**
 * The pool a material may be auto-substituted within when a variant attribute drives it —
 * the frontend twin of services/variants.substitution_groups, and the two must agree: the
 * server re-checks every rule against its own copy.
 *
 * A material type groups its materials (PLA with PLA, "Coasters" with "Coasters"). With no
 * type, a material pools with the rest of its category — but only when that category does not
 * track material types. For Blanks or Packaging the category is the sensible pool; for
 * filament an untyped spool is unfinished data, and pooling it with every filament is the
 * PLA-for-PETG hazard the type check exists to prevent. Tracking types on a category is what
 * turns it into subcategories.
 */
export function substitutionGroupKey(m: Material, categories: MaterialCategory[]): string | null {
  if (m.material_type_id != null) return `type:${m.material_type_id}`;
  if (m.category_id == null) return null;
  const category = categories.find((c) => c.id === m.category_id);
  return category && !category.tracks_material_type ? `category:${m.category_id}` : null;
}

/** Everything in the same pool as `base`, `base` included. */
export function groupMembers(base: Material, materials: Material[], categories: MaterialCategory[]): Material[] {
  const key = substitutionGroupKey(base, categories);
  if (key == null) return [];
  return materials.filter((m) => m.id === base.id || substitutionGroupKey(m, categories) === key);
}

/** Why a material can't drive substitutions, in the user's terms; null when it can. */
export function noGroupReason(base: Material, categories: MaterialCategory[]): string | null {
  if (substitutionGroupKey(base, categories) != null) return null;
  const category = categories.find((c) => c.id === base.category_id);
  return category == null
    ? `"${base.name}" has no category. Assign one (in Materials) before this can drive substitutions.`
    : `"${base.name}" has no Material Type set. Assign one (in Materials) to group its variants before this can drive substitutions.`;
}

function colourLabel(m: Material): string {
  return m.colour || m.name;
}

/** The leading " | "-separated segments every name shares, e.g. "Slate Coaster | " across the
 * Slate Coaster family — the part that says what the pool is, not which member this is. */
function sharedSegmentPrefix(names: string[]): string[] {
  const split = names.map((n) => n.split("|").map((s) => s.trim()));
  const first = split[0] ?? [];
  let n = 0;
  // Never consume a name's last segment: each label must keep something that tells it apart.
  while (n < first.length - 1 && split.every((parts) => parts.length > n + 1 && parts[n] === first[n])) n++;
  return first.slice(0, n);
}

/**
 * How each material in a pool is shown and keyed as an attribute value.
 *
 * Colour materials keep the colour-name logic: the colour where it identifies the material,
 * "Colour: full name" where another material in the list shares it. Everything else has no
 * colour to name, so it is labelled by its own name with the family prefix dropped — "Round
 * Natural Edge | 10x10cm" rather than a value (and a SKU suffix) that repeats "Slate Coaster"
 * on every variant.
 */
export function groupLabeller(members: Material[], categories: MaterialCategory[]): (m: Material) => string {
  const coloured = (m: Material) => categories.find((c) => c.id === m.category_id)?.tracks_colour ?? !!m.colour;
  if (members.some(coloured)) {
    const counts = new Map<string, number>();
    for (const m of members) counts.set(colourLabel(m), (counts.get(colourLabel(m)) ?? 0) + 1);
    return (m) => {
      const colour = colourLabel(m);
      return (counts.get(colour) ?? 0) > 1 && m.name !== colour ? `${colour}: ${m.name}` : colour;
    };
  }
  const prefix = members.length > 1 ? sharedSegmentPrefix(members.map((m) => m.name)) : [];
  return (m) => (prefix.length === 0 ? m.name : m.name.split("|").slice(prefix.length).join("|").trim());
}
