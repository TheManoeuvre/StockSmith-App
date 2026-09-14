import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { manufacturersApi } from "../../api/manufacturers";
import { suppliersApi } from "../../api/suppliers";
import { materialCategoriesApi } from "../../api/materialCategories";
import { materialTypesApi } from "../../api/materialTypes";
import { productCategoriesApi } from "../../api/productCategories";
import { coloursApi } from "../../api/colours";
import { ReferenceDataTable } from "../reference/ReferenceDataTable";

type ListId =
  | "manufacturers"
  | "suppliers"
  | "material-types"
  | "material-categories"
  | "product-categories"
  | "colours";

interface ListDef {
  id: ListId;
  label: string;
  navDescription: string;
  queryKey: readonly [string];
  list: () => Promise<{ id: number }[]>;
  render: () => ReactNode;
}

/**
 * A master/detail replacement for what used to be a stack of full-width reference-data tables.
 * The underlying editing surface (ReferenceDataTable) is unchanged — this just adds a way to
 * pick which table you're looking at, and a count badge so the choice is informed.
 */
export function ListsMasterDetail() {
  const [activeId, setActiveId] = useState<ListId>("manufacturers");

  const lists: ListDef[] = [
    {
      id: "manufacturers",
      label: "Manufacturers",
      navDescription: "Who makes a material.",
      queryKey: ["manufacturers"],
      list: manufacturersApi.list,
      render: () => (
        <ReferenceDataTable
          title="Manufacturers"
          description="Who makes a material. Renaming one updates every material that uses it."
          segment="manufacturers"
          queryKey={["manufacturers"]}
          api={{
            list: manufacturersApi.list,
            create: manufacturersApi.findOrCreate,
            update: manufacturersApi.update,
            remove: manufacturersApi.remove,
            merge: manufacturersApi.merge,
          }}
          fields={[
            { key: "name", label: "Name" },
            { key: "website_url", label: "Website", type: "url", placeholder: "https://…" },
          ]}
          usageLabel={(n) => `${n} material${n === 1 ? "" : "s"}`}
        />
      ),
    },
    {
      id: "suppliers",
      label: "Suppliers",
      navDescription: "Who you buy from. Carries its own lead time, which forecasting uses.",
      queryKey: ["suppliers"],
      list: suppliersApi.list,
      render: () => (
        <ReferenceDataTable
          title="Suppliers"
          description="Who you buy from. Renaming one updates every material and purchase that uses it. Default lead time feeds the materials time-to-stockout forecast — leave it blank to use the shop-wide default."
          segment="suppliers"
          queryKey={["suppliers"]}
          api={{
            list: suppliersApi.list,
            create: suppliersApi.findOrCreate,
            update: suppliersApi.update,
            remove: suppliersApi.remove,
            merge: suppliersApi.merge,
          }}
          fields={[
            { key: "name", label: "Name" },
            { key: "website_url", label: "Website", type: "url", placeholder: "https://…" },
            {
              key: "default_lead_time_days",
              label: "Default lead time (business days)",
              type: "number",
              placeholder: "Shop default",
            },
          ]}
          usageLabel={(n) => `${n} record${n === 1 ? "" : "s"}`}
        />
      ),
    },
    {
      id: "material-types",
      label: "Material types",
      navDescription: "PLA, PETG, resin, hardware, packaging.",
      queryKey: ["material-types"],
      list: materialTypesApi.list,
      render: () => (
        <ReferenceDataTable
          title="Material types"
          description="What a material is made of. Renaming one updates every material that uses it."
          segment="material-types"
          queryKey={["material-types"]}
          api={{
            list: materialTypesApi.list,
            create: materialTypesApi.findOrCreate,
            update: materialTypesApi.update,
            remove: materialTypesApi.remove,
            merge: materialTypesApi.merge,
          }}
          fields={[{ key: "name", label: "Name" }]}
          usageLabel={(n) => `${n} material${n === 1 ? "" : "s"}`}
        />
      ),
    },
    {
      id: "material-categories",
      label: "Material categories",
      navDescription: "Carries behaviour: colour, cost per kg, consumed by failed builds, kitting per order.",
      queryKey: ["material-categories"],
      list: materialCategoriesApi.list,
      render: () => (
        <ReferenceDataTable
          title="Material categories"
          description="What kind of thing a material is. The checkboxes are the behaviour that used to be hardcoded to filament and packaging — set them on any category, including ones you add. Order decides how the materials list groups and sorts."
          segment="material-categories"
          queryKey={["material-categories"]}
          api={{
            list: materialCategoriesApi.list,
            create: materialCategoriesApi.findOrCreate,
            update: materialCategoriesApi.update,
            remove: materialCategoriesApi.remove,
            merge: materialCategoriesApi.merge,
            reorder: materialCategoriesApi.reorder,
          }}
          fields={[
            { key: "name", label: "Name" },
            {
              key: "default_unit",
              label: "Default unit",
              type: "select",
              placeholder: "Leave unchanged",
              options: [
                { value: "g", label: "g" },
                { value: "ml", label: "ml" },
                { value: "each", label: "each" },
              ],
            },
            { key: "tracks_colour", label: "Has a colour", type: "checkbox" },
            { key: "tracks_material_type", label: "Has a material type", type: "checkbox" },
            { key: "cost_per_kg_display", label: "Show cost per kg", type: "checkbox" },
            { key: "consumed_on_failed_build", label: "Consumed by failed builds", type: "checkbox" },
            { key: "auto_kitting_per_order", label: "Kitting: one per order, not per unit", type: "checkbox" },
            { key: "show_in_kitting_bom_list", label: "Show in Kitting BOM list", type: "checkbox" },
          ]}
          usageLabel={(n) => `${n} material${n === 1 ? "" : "s"}`}
        />
      ),
    },
    {
      id: "product-categories",
      label: "Product categories",
      navDescription: "Groups products in the list, in counting and in stock takes.",
      queryKey: ["product-categories"],
      list: productCategoriesApi.list,
      render: () => (
        <ReferenceDataTable
          title="Product categories"
          description="What kind of thing a product is. Groups products for stock-count scheduling and for scoping a stock take. Renaming one updates every product that uses it."
          segment="product-categories"
          queryKey={["product-categories"]}
          api={{
            list: productCategoriesApi.list,
            create: productCategoriesApi.findOrCreate,
            update: productCategoriesApi.update,
            remove: productCategoriesApi.remove,
            merge: productCategoriesApi.merge,
          }}
          fields={[{ key: "name", label: "Name" }]}
          usageLabel={(n) => `${n} product${n === 1 ? "" : "s"}`}
        />
      ),
    },
    {
      id: "colours",
      label: "Colours",
      navDescription: "Promoted from free text so duplicates can be merged.",
      queryKey: ["colours"],
      list: coloursApi.list,
      render: () => (
        <ReferenceDataTable
          title="Colours"
          description="Promoted from free text, so the same colour on several materials is one entry you can rename or merge. Duplicates like 'Black' and 'black' were folded together when this table was created."
          segment="colours"
          queryKey={["colours"]}
          api={{
            list: coloursApi.list,
            create: coloursApi.findOrCreate,
            update: coloursApi.update,
            remove: coloursApi.remove,
            merge: coloursApi.merge,
          }}
          fields={[
            { key: "name", label: "Name" },
            { key: "hex_code", label: "Hex code", placeholder: "#ff00aa" },
          ]}
          usageLabel={(n) => `${n} material${n === 1 ? "" : "s"}`}
          rowLeading={(row) => (
            <span
              className="h-3.5 w-3.5 shrink-0 rounded-full border border-slate-300"
              style={{ backgroundColor: (row as { hex_code?: string }).hex_code ?? "transparent" }}
            />
          )}
        />
      ),
    },
  ];

  const active = lists.find((l) => l.id === activeId)!;

  return (
    <div className="flex items-start gap-4">
      <nav className="flex w-[196px] shrink-0 flex-col gap-0.5 rounded-[9px] border border-slate-200 bg-white p-2">
        {lists.map((list) => (
          <ListNavItem key={list.id} list={list} active={list.id === activeId} onSelect={() => setActiveId(list.id)} />
        ))}
      </nav>
      <div className="min-w-0 flex-1">{active.render()}</div>
    </div>
  );
}

function ListNavItem({ list, active, onSelect }: { list: ListDef; active: boolean; onSelect: () => void }) {
  const { data } = useQuery({ queryKey: list.queryKey, queryFn: list.list });
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex flex-col items-start gap-0.5 rounded-md px-2.5 py-2 text-left text-sm ${
        active ? "bg-slate-100 font-medium text-slate-900" : "text-slate-600 hover:bg-slate-50"
      }`}
    >
      <span className="flex w-full items-center justify-between gap-2">
        {list.label}
        {data && (
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-semibold text-slate-500">
            {data.length}
          </span>
        )}
      </span>
      <span className="text-[11.5px] font-normal leading-snug text-slate-500">{list.navDescription}</span>
    </button>
  );
}
