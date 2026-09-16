import { PurchaseStatusPill } from "stocksmith-ui";

/** The three purchase-order states, as shown in the Purchases list and detail header. */
export const AllStatuses = () => (
  <div className="flex items-center gap-2">
    <PurchaseStatusPill status="ordered" />
    <PurchaseStatusPill status="partially_received" />
    <PurchaseStatusPill status="received" />
  </div>
);

/** In context: a purchase row's supplier, reference and status. */
export const InRow = () => (
  <div className="flex w-[420px] items-center justify-between rounded border border-slate-200 bg-white px-3 py-2 text-sm">
    <div>
      <div className="font-medium text-slate-900">Ashford Timber Supplies</div>
      <div className="text-xs text-slate-500">PO-0142 · 3 lines</div>
    </div>
    <PurchaseStatusPill status="partially_received" />
  </div>
);
