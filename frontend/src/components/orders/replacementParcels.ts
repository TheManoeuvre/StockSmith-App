import type { ReplacementParcelReason } from "../../api/types";

export const REASON_LABELS: Record<ReplacementParcelReason, string> = {
  faulty_item: "Faulty item",
  missing_from_order: "Missing from order",
  lost_in_transit: "Lost in transit",
  damaged_in_transit: "Damaged in transit",
  other: "Other",
  unspecified: "Reason not set",
};

/** The reasons a user can pick. `unspecified` is only ever set by the sync, for a parcel
 *  it auto-created from a second marketplace label — the whole point of the review prompt
 *  is to replace it. */
export const SELECTABLE_REASONS: ReplacementParcelReason[] = [
  "faulty_item",
  "missing_from_order",
  "lost_in_transit",
  "damaged_in_transit",
  "other",
];
