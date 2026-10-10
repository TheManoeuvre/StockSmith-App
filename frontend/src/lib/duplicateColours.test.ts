import { expect, it } from "vitest";
import { findDuplicateColours } from "./duplicateColours";

const mat = (over: Record<string, unknown>) =>
  ({ is_active: true, material_type_id: 3, material_type_name: "PETG", colour: "White", ...over }) as never;

it("groups active materials of one type that share a colour, ignoring case and spacing", () => {
  const groups = findDuplicateColours([
    mat({ id: 9, name: "Sunlu" }),
    mat({ id: 56, name: "Bambu", colour: " white " }),
    mat({ id: 17, name: "Black one", colour: "Black" }),
  ]);
  expect(groups).toHaveLength(1);
  expect(groups[0].materials.map((m) => m.id)).toEqual([9, 56]);
});

it("does not group across types, retired materials, or materials with no type or colour", () => {
  expect(
    findDuplicateColours([
      mat({ id: 1 }),
      mat({ id: 2, material_type_id: 4, material_type_name: "PLA" }), // same colour, other type
      mat({ id: 3, is_active: false }), // retired
      mat({ id: 4, material_type_id: null }),
      mat({ id: 5, material_type_id: null }),
      mat({ id: 6, colour: null }),
      mat({ id: 7, colour: null }),
    ]),
  ).toEqual([]);
});

it("lists the oldest material first", () => {
  const [group] = findDuplicateColours([mat({ id: 56 }), mat({ id: 9 })]);
  expect(group.materials.map((m) => m.id)).toEqual([9, 56]);
});
