import type { PlatformLimitConflictsDetail, PlatformConflictResolution } from "../../api/types";
import { ApiError } from "../../api/client";
import { ConfirmDialog } from "../common/ConfirmDialog";

/**
 * Picks the platform-limit 409 out of a failed variant save, or null for any other error.
 *
 * The server knows the numbers — how many attributes or active variants the product would
 * end up with, and what each target store allows — but not whether the user means to list
 * there. So it lists the conflicts and waits; everything else is a plain ErrorBanner message.
 */
export function platformConflictDetail(error: unknown): PlatformLimitConflictsDetail | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = error.detail as Partial<PlatformLimitConflictsDetail> | undefined;
  return detail?.code === "platform_limit_conflicts" && Array.isArray(detail.conflicts)
    ? (detail as PlatformLimitConflictsDetail)
    : null;
}

/**
 * "This would exceed a store's limit — save anyway?"
 *
 * Two-way, unlike the shared-material dialog: there is no partial answer to "too many".
 * Saving is the default-toned action rather than red because nothing is destroyed; the
 * product just can't be listed on that store until something changes.
 */
export function PlatformConflictDialog({
  detail,
  busy,
  onResolve,
  onCancel,
}: {
  detail: PlatformLimitConflictsDetail;
  busy: boolean;
  onResolve: (resolution: Exclude<PlatformConflictResolution, "ask">) => void;
  onCancel: () => void;
}) {
  return (
    <ConfirmDialog
      open
      title="Over a platform limit"
      tone="default"
      confirmLabel="Save anyway"
      busy={busy}
      onConfirm={() => onResolve("proceed")}
      onCancel={onCancel}
      body={
        <>
          <ul className="flex flex-col gap-1 rounded border border-amber-200 bg-amber-50 p-2">
            {detail.conflicts.map((c) => (
              <li key={`${c.platform}-${c.field}`} className="text-sm">
                {c.message}
              </li>
            ))}
          </ul>
          <p className="text-slate-600">
            Nothing has been saved yet. If you save anyway, this product can't be listed on{" "}
            {detail.conflicts.length === 1 ? "that store" : "those stores"} until it's brought within the limit,
            the store is excluded in the product's platform settings, or the limit is raised in Settings.
          </p>
        </>
      }
    />
  );
}
