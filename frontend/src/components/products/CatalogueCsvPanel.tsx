import { productsApi } from "../../api/products";
import { CsvImportExport } from "../common/CsvImportExport";
import { HelpLink } from "../help/HelpLink";

const INVALIDATE = ["products", "product", "variants", "dashboard-summary"];

const FILES = [
  {
    title: "1. Products",
    description: "Name, SKU and description. The SKU is how the other two files find each product.",
    onExport: productsApi.exportCsv,
    onImport: productsApi.importCsv,
  },
  {
    title: "2. Variants",
    description: "One row per variant: its product's SKU, its name, attributes and price.",
    onExport: productsApi.exportVariantsCsv,
    onImport: productsApi.importVariantsCsv,
  },
  {
    title: "3. Recipes (BOMs)",
    description:
      "One row per material in a recipe. Leave the variant blank for the product's own recipe. Importing replaces each recipe in the file as a whole.",
    onExport: productsApi.exportBomsCsv,
    onImport: productsApi.importBomsCsv,
  },
];

/**
 * The Products page's spreadsheet import/export, behind one button rather than three pairs in the
 * header. Ordered the way imports have to run: variants and recipes refer to products by SKU.
 */
export function CatalogueCsvPanel() {
  return (
    <section aria-label="Import and export" className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4">
      <p className="text-sm text-slate-600">
        Export a file to get the right columns, edit it in a spreadsheet, then import it back. Import in this
        order. <HelpLink doc="getting-started" anchor="5-add-your-products-and-their-recipes">More about importing</HelpLink>
      </p>
      <div className="grid gap-4 md:grid-cols-3">
        {FILES.map((file) => (
          <div key={file.title} className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold text-slate-900">{file.title}</h3>
            <p className="text-xs text-slate-500">{file.description}</p>
            <CsvImportExport onExport={file.onExport} onImport={file.onImport} invalidateKey={INVALIDATE} />
          </div>
        ))}
      </div>
    </section>
  );
}
