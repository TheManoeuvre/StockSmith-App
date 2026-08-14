import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { listingProfilesApi } from "../../api/listingProfiles";
import type { ListingPlatform } from "../../api/types";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";

/**
 * Shows what would be created, then asks before creating it.
 *
 * The acknowledgement checkbox is not ceremony. This is the only thing in StockSmith that
 * makes a new object on a marketplace, and an Etsy draft cannot be deleted from here — if
 * it turns out to be wrong, the only way to remove it is to open Etsy. That is the same
 * reasoning behind the confirmation on eBay listing migration, which is likewise
 * one-directional.
 *
 * Nothing here publishes. The draft is a starting point the seller finishes in Etsy's own
 * editor, and StockSmith has no way to make it live.
 */
export function DraftListingModal({
  productId,
  platform,
  onClose,
}: {
  productId: number;
  platform: ListingPlatform;
  onClose: () => void;
}) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();
  const [acknowledged, setAcknowledged] = useState(false);

  const { data: readiness } = useQuery({
    queryKey: ["platforms", platform, "products", productId, "draft-readiness"],
    queryFn: () => listingProfilesApi.draftReadiness(platform, productId),
  });

  const createMutation = useMutation({
    mutationFn: () => listingProfilesApi.createDraft(platform, productId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["platforms", platform, "products", productId] });
    },
  });

  const blockers = readiness?.issues.filter((i) => i.severity === "blocker") ?? [];
  const warnings = readiness?.issues.filter((i) => i.severity === "warning") ?? [];
  const result = createMutation.data;

  return (
    <Modal
      title={result ? `Draft created on ${label}` : `Create a ${label} draft`}
      maxWidth="max-w-lg"
      onClose={createMutation.isPending ? () => {} : onClose}
      footer={
        result ? (
          <button onClick={onClose} className="rounded border border-slate-300 px-4 py-2 text-sm">
            Done
          </button>
        ) : (
          <>
            <button
              autoFocus
              onClick={onClose}
              disabled={createMutation.isPending}
              className="rounded border border-slate-300 px-4 py-2 text-sm disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={() => createMutation.mutate()}
              disabled={!acknowledged || blockers.length > 0 || createMutation.isPending}
              className="rounded bg-slate-900 px-4 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {createMutation.isPending ? "Creating…" : `Create draft on ${label}`}
            </button>
          </>
        )
      }
    >
      <div className="flex flex-col gap-3 text-sm">
        <ErrorBanner error={createMutation.error} />

        {result ? (
          <>
            <p>
              Listing <span className="font-mono">{result.external_listing_id}</span> was created as a{" "}
              <strong>{result.state}</strong>. It isn't visible to buyers — finish it in {label} and publish
              from there.
            </p>
            {result.warnings.length > 0 && (
              <ul className="list-inside list-disc text-xs text-amber-800">
                {result.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            {result.publish_blockers.length > 0 && (
              <div className="rounded border border-amber-300 bg-amber-50 p-2 text-xs">
                <p className="font-medium">Before you can publish it:</p>
                <ul className="list-inside list-disc">
                  {result.publish_blockers.map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </div>
            )}
          </>
        ) : (
          <>
            {readiness && (
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                <dt className="text-slate-500">Title</dt>
                <dd>{readiness.title}</dd>
                <dt className="text-slate-500">Description</dt>
                <dd>{readiness.description_chars} characters</dd>
                <dt className="text-slate-500">Profile</dt>
                <dd>{readiness.profile_name ?? "—"}</dd>
                <dt className="text-slate-500">Variations</dt>
                <dd>
                  {readiness.priced_unit_count} of {readiness.unit_count} priced
                </dd>
                <dt className="text-slate-500">Images</dt>
                <dd>{readiness.image_count}</dd>
              </dl>
            )}

            {blockers.length > 0 && (
              <ul className="list-inside list-disc text-xs text-red-700">
                {blockers.map((issue, i) => (
                  <li key={i}>{issue.message}</li>
                ))}
              </ul>
            )}
            {warnings.length > 0 && (
              <ul className="list-inside list-disc text-xs text-amber-800">
                {warnings.map((issue, i) => (
                  <li key={i}>{issue.message}</li>
                ))}
              </ul>
            )}

            <label className="flex items-start gap-2 rounded border border-slate-200 bg-slate-50 p-2 text-xs">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                I understand this creates a real draft in my {label} shop. It won't be visible to buyers,
                but it <strong>can't be deleted from StockSmith</strong> — only from {label}.
              </span>
            </label>
          </>
        )}
      </div>
    </Modal>
  );
}
