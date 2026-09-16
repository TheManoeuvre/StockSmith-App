import { BarcodeLabel } from "stocksmith-ui";

/** A 50 x 25 mm product label as sent to the label printer. */
export const ProductLabel = () => (
  <div className="w-64 rounded border border-dashed border-slate-300 bg-white">
    <BarcodeLabel name="Walnut coaster set" sku="CST-WAL-4" barcode="CST-WAL-4" />
  </div>
);

export const NoSku = () => (
  <div className="w-64 rounded border border-dashed border-slate-300 bg-white">
    <BarcodeLabel name="PLA - matte black" barcode="MAT-000128" />
  </div>
);
