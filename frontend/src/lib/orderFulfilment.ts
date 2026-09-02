import type { Order } from "../api/types";

export interface OrderFulfilment {
  /** Short state name for the list column / the slide-over's Fulfilment stat tile. */
  label: string;
  /** Tailwind text-colour class for the label and its status dot. */
  toneClass: string;
  /** The one-line explanation under the label. */
  detail: string;
  /** The inline action the row offers, if any. "open" just opens the slide-over. */
  action?: { label: string; kind: "allocate" | "ship" | "open" };
}

/**
 * Derives an order's fulfilment state from what's already on the `Order` + its `lines` — no
 * extra fetch. Mirrors the reviewed design's Fulfilment column (state dot · label · detail ·
 * action button), mapped onto the fields the API actually returns (allocated/shipped/mapping
 * per line, not the design's buildable/packaging-short signals which aren't on the payload).
 */
export function orderFulfilment(order: Order): OrderFulfilment {
  if (order.status === "shipped") {
    return {
      label: "Shipped",
      toneClass: "text-slate-500",
      detail: order.shipping_profile_name ?? "no shipping profile",
    };
  }
  if (order.status === "cancelled") {
    return { label: "Cancelled", toneClass: "text-slate-400", detail: "" };
  }

  const unmapped = order.lines.filter((l) => l.needs_mapping).length;
  if (unmapped > 0) {
    return {
      label: "Needs mapping",
      toneClass: "text-amber-700",
      detail: `Map ${unmapped} SKU${unmapped === 1 ? "" : "s"} to fulfil`,
      action: { label: "Fix", kind: "open" },
    };
  }

  const ordered = order.lines.reduce((sum, l) => sum + l.ordered_qty, 0);
  const allocated = order.lines.reduce((sum, l) => sum + l.allocated_qty, 0);

  if (order.lines.length > 0 && order.lines.every((l) => l.allocated_qty >= l.ordered_qty)) {
    return {
      label: "Ready to ship",
      toneClass: "text-emerald-700",
      detail: "All units allocated",
      action: { label: "Ship", kind: "ship" },
    };
  }
  if (allocated > 0) {
    return {
      label: "Part allocated",
      toneClass: "text-blue-700",
      detail: `${allocated} of ${ordered} units`,
      action: { label: "Allocate", kind: "allocate" },
    };
  }
  return {
    label: "Not allocated",
    toneClass: "text-slate-500",
    detail: "Nothing reserved yet",
    action: { label: "Allocate", kind: "allocate" },
  };
}
