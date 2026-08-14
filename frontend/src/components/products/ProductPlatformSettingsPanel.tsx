import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { listingProfilesApi } from "../../api/listingProfiles";
import { platformConfigApi } from "../../api/platformConfig";
import type { ListingPlatform } from "../../api/types";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ErrorBanner } from "../common/ErrorBanner";

/**
 * Per-product listing setup: which profile applies, the listing copy, and whether a draft
 * could be created right now.
 *
 * The readiness report drives this rather than decorating it. It's local-only — no
 * marketplace call — so it can load with the page and say plainly what's missing before
 * the user goes looking.
 */
export function ProductPlatformSettingsPanel({
  productId,
  platform,
}: {
  productId: number;
  platform: ListingPlatform;
}) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);

  const { data: settings } = useQuery({
    queryKey: ["platforms", platform, "products", productId, "settings"],
    queryFn: () => listingProfilesApi.getProductSettings(platform, productId),
    enabled: open,
  });
  const { data: profiles } = useQuery({
    queryKey: ["settings", "listing-profiles", platform],
    queryFn: () => listingProfilesApi.list(platform),
    enabled: open,
  });
  const { data: readiness } = useQuery({
    queryKey: ["platforms", platform, "products", productId, "draft-readiness"],
    queryFn: () => listingProfilesApi.draftReadiness(platform, productId),
  });
  const { data: limits } = useQuery({
    queryKey: ["settings", "platform-limits", platform],
    queryFn: () => platformConfigApi.listLimits(platform),
    enabled: open,
  });

  const [title, setTitle] = useState<string | null>(null);
  const [description, setDescription] = useState<string | null>(null);
  const [profileId, setProfileId] = useState<number | null | undefined>(undefined);

  const titleCap = Number(limits?.find((l) => l.field === "title_max_length")?.effective_value ?? 0);
  const currentTitle = title ?? settings?.listing_title ?? "";

  const saveMutation = useMutation({
    mutationFn: () =>
      listingProfilesApi.saveProductSettings(platform, productId, {
        listing_profile_id: profileId === undefined ? settings?.listing_profile_id ?? null : profileId,
        is_target: settings?.is_target ?? null,
        listing_title: title ?? settings?.listing_title ?? null,
        listing_description: description ?? settings?.listing_description ?? null,
      }),
    onSuccess: () => {
      setTitle(null);
      setDescription(null);
      setProfileId(undefined);
      queryClient.invalidateQueries({ queryKey: ["platforms", platform, "products", productId] });
    },
  });

  const blockers = readiness?.issues.filter((i) => i.severity === "blocker") ?? [];
  const warnings = readiness?.issues.filter((i) => i.severity === "warning") ?? [];

  return (
    <div className="flex flex-col gap-2 rounded border border-slate-200 bg-white p-3 text-sm">
      <button onClick={() => setOpen((v) => !v)} className="flex items-center justify-between text-left">
        <span className="flex items-center gap-2">
          <span className="font-medium">{label} listing setup</span>
          {readiness && (
            <span
              className={`rounded px-2 py-0.5 text-xs ${
                readiness.can_create ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"
              }`}
            >
              {readiness.can_create ? "Ready to draft" : `${blockers.length} thing(s) missing`}
            </span>
          )}
        </span>
        <span className="text-slate-500">{open ? "Hide" : "Show"}</span>
      </button>

      {blockers.length > 0 && (
        <ul className="list-inside list-disc text-xs text-red-700">
          {blockers.map((issue, i) => (
            <li key={`${issue.field}-${i}`}>
              {issue.message}
              {issue.fix_hint && <span className="text-slate-600"> {issue.fix_hint}</span>}
            </li>
          ))}
        </ul>
      )}

      {open && (
        <>
          {warnings.length > 0 && (
            <ul className="list-inside list-disc text-xs text-amber-800">
              {warnings.map((issue, i) => (
                <li key={`${issue.field}-${i}`}>{issue.message}</li>
              ))}
            </ul>
          )}

          <label className="flex flex-col gap-1 text-xs">
            <span>Listing profile</span>
            <select
              className="rounded border border-slate-300 px-2 py-1"
              value={(profileId === undefined ? settings?.listing_profile_id : profileId) ?? ""}
              onChange={(e) => setProfileId(e.target.value === "" ? null : Number(e.target.value))}
            >
              <option value="">Use the default</option>
              {profiles?.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name}
                  {profile.is_default ? " (default)" : ""}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-xs">
            <span className="flex items-center justify-between">
              <span>{label} listing title</span>
              {titleCap > 0 && (
                <span className={currentTitle.length > titleCap ? "text-red-700" : "text-slate-500"}>
                  {currentTitle.length} / {titleCap}
                </span>
              )}
            </span>
            <input
              className="rounded border border-slate-300 px-2 py-1"
              value={currentTitle}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={settings?.resolved_title ?? ""}
            />
            {/* Naming where a fallback came from stops it reading as authored copy. */}
            {settings && !settings.listing_title && (
              <span className="text-slate-500">
                Using{" "}
                {settings.resolved_title_source === "shared"
                  ? "the product's listing title"
                  : "the product name"}
                : {settings.resolved_title}
              </span>
            )}
          </label>

          <label className="flex flex-col gap-1 text-xs">
            <span>{label} listing description</span>
            <textarea
              className="min-h-20 rounded border border-slate-300 px-2 py-1"
              value={description ?? settings?.listing_description ?? ""}
              onChange={(e) => setDescription(e.target.value)}
            />
            {settings && !settings.listing_description && (
              <span className="text-slate-500">
                {settings.resolved_description
                  ? `Using the product description (${settings.resolved_description.length} chars).`
                  : "No description anywhere — a listing needs one."}
              </span>
            )}
          </label>

          <ErrorBanner error={saveMutation.error} />
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="self-start rounded border border-slate-400 px-3 py-1 text-xs disabled:opacity-50"
          >
            {saveMutation.isPending ? "Saving…" : "Save"}
          </button>
        </>
      )}
    </div>
  );
}
