import { expect, it } from "vitest";
import type { Material, MaterialCategory } from "../api/types";
import { groupLabeller, groupMembers, noGroupReason, substitutionGroupKey } from "./substitutionGroups";

const cat = (id: number, name: string, tracks_colour: boolean, tracks_material_type: boolean) =>
  ({ id, name, tracks_colour, tracks_material_type }) as MaterialCategory;
const mat = (id: number, name: string, category_id: number, extra: Partial<Material> = {}) =>
  ({ id, name, category_id, is_active: true, colour: null, material_type_id: null, ...extra }) as Material;

const FILAMENT = cat(1, "Filament", true, true);
const BLANKS = cat(2, "Blanks", false, false);
const KEYRINGS = cat(3, "Keyrings", false, true);
const cats = [FILAMENT, BLANKS, KEYRINGS];

const round = mat(10, "Slate Coaster | Round Natural Edge | 10cm", 2);
const square = mat(11, "Slate Coaster | Square Natural Edge | 10x10cm", 2);
const wood = mat(12, "Wood Coaster | Square | 10x10cm", 2);

it("pools an untyped material with its category when the category has no types", () => {
  expect(substitutionGroupKey(round, cats)).toBe("category:2");
  expect(groupMembers(round, [round, square, wood, mat(13, "Brown Box", 4)], cats).map((m) => m.id)).toEqual([
    10, 11, 12,
  ]);
});

it("a type takes precedence, and splits a category into subcategories", () => {
  const typed = mat(20, "Ring A", 3, { material_type_id: 7 });
  const typed2 = mat(21, "Ring B", 3, { material_type_id: 7 });
  const other = mat(22, "Ring C", 3, { material_type_id: 8 });
  expect(groupMembers(typed, [typed, typed2, other], cats).map((m) => m.id)).toEqual([20, 21]);
});

it("an untyped material in a type-tracking category has no pool, and says why", () => {
  const spool = mat(30, "Mystery spool", 1, { colour: "Red" });
  expect(substitutionGroupKey(spool, cats)).toBeNull();
  expect(noGroupReason(spool, cats)).toMatch(/no Material Type/);
});

it("labels non-colour materials by name with the shared family prefix dropped", () => {
  const label = groupLabeller([round, square], cats);
  expect(label(round)).toBe("Round Natural Edge | 10cm");
  expect(label(square)).toBe("Square Natural Edge | 10x10cm");
  // No common prefix: names are kept whole.
  expect(groupLabeller([round, wood], cats)(wood)).toBe("Wood Coaster | Square | 10x10cm");
});

it("keeps the colour naming for colour categories", () => {
  const a = mat(40, "Brand A Red", 1, { colour: "Red", material_type_id: 1 });
  const b = mat(41, "Brand B Red", 1, { colour: "Red", material_type_id: 1 });
  const c = mat(42, "Blue PLA", 1, { colour: "Blue", material_type_id: 1 });
  const label = groupLabeller([a, b, c], cats);
  expect(label(c)).toBe("Blue");
  expect(label(a)).toBe("Red: Brand A Red");
});
