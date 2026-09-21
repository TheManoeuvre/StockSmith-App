import type { LiveListingConflictsDetail, LiveListingResolution } from "../../api/types";
import { ApiError } from "../../api/client";
import { ConfirmDialog } from "../common/ConfirmDialog";

/**
 * Picks the live-listing 409 out of a failed merge, or null for any other error.
 *
 * Same contract as platformConflictDetail: the server knows the variant is on sale
 * somewhere but not whether the user is ready to take it off, so it lists the listings and
 * waits.
 */
export function liveListingConflictDetail(error: unknown): LiveListingConflictsDetail | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = error.detail as Partial<LiveListingConflictsDetail> | undefined;
  return detail?.code === "live_listing_conflicts" && Array.isArray(detail.conflicts)
    ? (detail as LiveListingConflictsDetail)
    : null;
}

/**
 * "This variant is on sale — merge anyway?"
 *
 * Confirming pushes a zero quantity to each listed variation before the variant is
 * disabled, so nothing stays on sale that can no longer be picked. Red, unlike the
 * platform-limit dialog: this one does change something on the marketplace.
 */
export function LiveListingConflictDialog({
  detail,
  busy,
  onResolve,
  onCancel,
}: {
  detail: LiveListingConflictsDetail;
  busy: boolean;
  onResolve: (resolution: Exclude<LiveListingResolution, "ask">) => void;
  onCancel: () => void;
}) {
  return (
    <ConfirmDialog
      open
      title="Live on a marketplace"
      confirmLabel="Set to 0 and merge"
      busy={busy}
      onConfirm={() => onResolve("proceed")}
      onCancel={onCancel}
      body={
        <>
          <ul className="flex flex-col gap-1 rounded border border-amber-200 bg-amber-50 p-2">
            {detail.conflicts.map((c) => (
              <li key={`${c.platform}-${c.external_listing_id}`} className="text-sm">
                {c.message}
              </li>
            ))}
          </ul>
          <p className="text-slate-600">
            Nothing has been changed yet. Merging sets each of these variations to a quantity of 0 so it stops
            selling; the variation itself stays on the listing until you remove it there.
          </p>
        </>
      }
    />
  );
}
