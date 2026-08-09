import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { shippingProfilesApi } from "../../api/shippingProfiles";
import type { ShippingProfile } from "../../api/types";
import { ReferenceDataTable } from "../reference/ReferenceDataTable";

/**
 * Shipping profiles, on the same accordion as the other reference data.
 *
 * They are reference data — a named row that products, variants and orders point at. They sat
 * under Pricing only because their per-channel cost columns looked like a pricing concern; those
 * are keyed by (platform × profile), so they belong on the profile itself.
 *
 * One thing differs from manufacturers and material types, and it's why this stays a component
 * rather than another `<ReferenceDataTable>` call in settings.tsx: orders reference a profile.
 * Deleting or merging one would rewrite what a shipped order says it was shipped under. So
 * delete is only offered when nothing references the profile at all, and retiring one you no
 * longer offer is a separate Archive action that leaves every existing reference intact.
 *
 * This also gains the unsaved-changes guard it never had. Previously each row kept its own local
 * state with a Save button and no registration, so typing a cost and navigating away lost it
 * silently.
 */
export function ShippingProfileSettings() {
  const queryClient = useQueryClient();
  const [showArchived, setShowArchived] = useState(false);

  const queryKey = ["settings", "shipping-profiles", showArchived] as const;
  const { data: profiles } = useQuery({
    queryKey,
    queryFn: () => shippingProfilesApi.list(showArchived),
  });

  const archiveMutation = useMutation({
    mutationFn: ({ id, archived }: { id: number; archived: boolean }) =>
      shippingProfilesApi.update(id, { is_archived: archived }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings", "shipping-profiles"] });
      queryClient.invalidateQueries({ queryKey: ["shipping-profiles"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });

  const hasArchived = (profiles ?? []).some((p) => p.is_archived);

  return (
    <div className="flex flex-col gap-2">
      <ReferenceDataTable<ShippingProfile>
        title="Shipping profiles"
        description="What you charge for postage, and what it actually costs you per channel. Products default to one; orders snapshot the cost for their own channel when they ship, so historical profit doesn't drift."
        segment="shipping-profiles"
        queryKey={queryKey}
        api={{
          list: () => shippingProfilesApi.list(showArchived),
          create: (name: string) => shippingProfilesApi.create({ name }),
          update: shippingProfilesApi.update,
          remove: shippingProfilesApi.remove,
          merge: shippingProfilesApi.merge,
        }}
        fields={[
          { key: "name", label: "Name" },
          { key: "price", label: "Price charged", type: "money" },
          { key: "cost_etsy", label: "Cost (Etsy)", type: "money" },
          { key: "cost_ebay", label: "Cost (eBay)", type: "money" },
          { key: "cost_manual", label: "Cost (manual)", type: "money" },
        ]}
        usageLabel={(n) => `${n} record${n === 1 ? "" : "s"}`}
        extraRowActions={(profile) => (
          <button
            type="button"
            onClick={() => archiveMutation.mutate({ id: profile.id, archived: !profile.is_archived })}
            disabled={archiveMutation.isPending}
            className="rounded border border-slate-300 px-2 py-1 text-sm disabled:opacity-50"
          >
            {profile.is_archived ? "Restore to list" : "Archive"}
          </button>
        )}
      />

      <label className="flex items-center gap-2 self-start text-sm text-slate-500">
        <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
        Show archived profiles
        {!showArchived && hasArchived && " (some are hidden)"}
      </label>
    </div>
  );
}
