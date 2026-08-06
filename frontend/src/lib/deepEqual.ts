// Structural equality for the JSON-shaped values held in editor state (BOM line arrays,
// override records, form field objects). Used by useEditableCopy to decide whether an
// editor is dirty.
//
// Deliberately not `JSON.stringify(a) === JSON.stringify(b)`: stringify is key-order
// sensitive, and BomOverrideEditor builds its additive lines at two different sites whose
// object literals list keys in different orders — that comparison would report a
// permanently-dirty editor.
export function deepEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;

  // Object.is separates null from undefined and treats NaN as equal to itself; past this
  // point anything non-object (or a null vs object mismatch) is simply unequal.
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;

  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((item, i) => deepEqual(item, b[i]));
  }

  const aRecord = a as Record<string, unknown>;
  const bRecord = b as Record<string, unknown>;
  const aKeys = Object.keys(aRecord);
  if (aKeys.length !== Object.keys(bRecord).length) return false;
  return aKeys.every(
    (key) => Object.prototype.hasOwnProperty.call(bRecord, key) && deepEqual(aRecord[key], bRecord[key])
  );
}
