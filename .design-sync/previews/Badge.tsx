import { Badge } from "stocksmith-ui";

/** The purchase-status vocabulary from PurchaseStatusPill. */
export const PurchaseStatus = () => (
  <div className="flex items-center gap-2">
    <Badge className="bg-amber-100 text-amber-800">Ordered</Badge>
    <Badge className="bg-sky-100 text-sky-800">Part received</Badge>
    <Badge className="bg-green-100 text-green-800">Received</Badge>
  </div>
);

/** Time-to-stockout urgency, as used in the Materials detail header. */
export const StockoutStatus = () => (
  <div className="flex items-center gap-2">
    <Badge className="bg-red-100 text-red-800">Critical</Badge>
    <Badge className="bg-amber-100 text-amber-800">Warning</Badge>
    <Badge className="bg-emerald-100 text-emerald-800">OK</Badge>
    <Badge className="bg-slate-100 text-slate-600">Not enough data</Badge>
  </div>
);

/** Neutral badge beside a heading, e.g. an inactive product. */
export const BesideHeading = () => (
  <h2 className="flex items-center text-lg font-semibold text-slate-900">
    Walnut coaster set
    <Badge className="ml-2 bg-slate-100 text-slate-600">Inactive</Badge>
  </h2>
);
