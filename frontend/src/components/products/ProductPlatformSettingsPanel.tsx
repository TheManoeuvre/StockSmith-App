import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { listingProfilesApi, type ProductPlatformSettings } from "../../api/listingProfiles";
import { platformConfigApi } from "../../api/platformConfig";
import type { ListingPlatform } from "../../api/types";
import { DirtyPath, useManagedSave } from "../../hooks/useDirtyRegistry";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { useGuard } from "../../hooks/useUnsavedChangesGuard";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveButton } from "../common/SaveButton";
import { ListingProfileSummary } from "./ListingProfileSummary";

interface ListingSetupForm {
  profileId: number | null;
  title: string;
  description: string;
}

// Nulls become empty strings so the inputs are always controlled; the backend strips a
// blank back to null on save, so the round trip is lossless.
function toForm(settings: ProductPlatformSettings): ListingSetupForm {
  return {
    profileId: settings.listing_profile_id ?? null,
    title: settings.listing_title ?? "",
    description: settings.listing_description ?? "",
  };
}

/**
 * Per-product listing setup: which profile applies, the listing copy, and whether a draft
 * could be created right now.
 *
 * The profile is a choice, not a fallback: there is no platform default, so a product has
 * no profile until one is picked here, and the picker is followed by what that pick
 * commits the listing to (category, processing profile, policies) so the consequence is
 * visible at the moment of choosing rather than after the draft exists.
 *
 * The readiness report drives this rather than decorating it. It's local-only — no
 * marketplace call — so it can load with the page and say plainly what's missing before
 * the user goes looking.
 */
export function ProductPlatformSettingsPanel(props: { productId: number; platform: ListingPlatform }) {
  // The path segment has to be provided above the editor that registers under it.
  return (
    <DirtyPath segment={`stores/${props.platform}`}>
      <PanelBody {...props} />
    </DirtyPath>
  );
}

function PanelBody({ productId, platform }: { productId: number; platform: ListingPlatform }) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();
  const guard = useGuard();
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

  // Buffered like every other product editor, so the footer Save / Revert and the
  // unsaved-changes guard see it. The settings query only runs once expanded, so the form
  // seeds on first open and stays put across refetches after that.
  const { value: form, setValue: setForm, isDirty, markSaved, revert } = useEditableCopy<ListingSetupForm>({
    key: "listing-setup",
    label: `${label} listing setup`,
    initial: { profileId: null, title: "", description: "" },
    seed: settings ? toForm(settings) : undefined,
    seedKey: productId,
  });
  const patch = (changes: Partial<ListingSetupForm>) => setForm((f) => ({ ...f, ...changes }));

  const titleCap = Number(limits?.find((l) => l.field === "title_max_length")?.effective_value ?? 0);

  const saveMutation = useMutation({
    mutationFn: () =>
      listingProfilesApi.saveProductSettings(platform, productId, {
        listing_profile_id: form.profileId,
        is_target: settings?.is_target ?? null,
        listing_title: form.title,
        listing_description: form.description,
      }),
    onSuccess: (saved) => {
      // The server trims and nulls blanks; baselining on its copy keeps the form clean.
      markSaved(toForm(saved));
      queryClient.invalidateQueries({ queryKey: ["platforms", platform, "products", productId] });
    },
  });
  const managed = useManagedSave("listing-setup", {
    save: () => saveMutation.mutate(),
    revert,
  });
  const saveStatus = useSaveStatus(saveMutation.status);

  // Collapsing hides the inputs but not the buffered edits — so ask first, and discard
  // them on the way out rather than leaving invisible dirty state behind.
  const toggleOpen = () => {
    if (!open) {
      setOpen(true);
      return;
    }
    guard.attempt(
      () => {
        revert();
        setOpen(false);
      },
      { prefix: `stores/${platform}/` }
    );
  };

  const selectedProfile = profiles?.find((p) => p.id === form.profileId) ?? null;

  const blockers = readiness?.issues.filter((i) => i.severity === "blocker") ?? [];
  const warnings = readiness?.issues.filter((i) => i.severity === "warning") ?? [];

  return (
    <div className="flex flex-col gap-2 rounded border border-slate-200 bg-white p-3 text-sm">
      <button onClick={toggleOpen} className="flex items-center justify-between text-left">
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

          <div className="flex flex-col gap-1 text-xs">
            <label className="flex flex-col gap-1">
              <span>Listing profile</span>
              <select
                className="rounded border border-slate-300 px-2 py-1"
                value={form.profileId ?? ""}
                onChange={(e) => patch({ profileId: e.target.value === "" ? null : Number(e.target.value) })}
              >
                <option value="">Choose a profile…</option>
                {profiles?.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </label>
            {selectedProfile ? (
              <ListingProfileSummary profile={selectedProfile} />
            ) : (
              profiles && (
                <span className="text-slate-500">
                  {profiles.length === 0
                    ? `No ${label} profiles yet — create one in Settings › Integrations.`
                    : "Nothing is applied until a profile is chosen."}
                </span>
              )
            )}
          </div>

          <label className="flex flex-col gap-1 text-xs">
            <span className="flex items-center justify-between">
              <span>{label} listing title</span>
              {titleCap > 0 && (
                <span className={form.title.length > titleCap ? "text-red-700" : "text-slate-500"}>
                  {form.title.length} / {titleCap}
                </span>
              )}
            </span>
            <input
              className="rounded border border-slate-300 px-2 py-1"
              value={form.title}
              onChange={(e) => patch({ title: e.target.value })}
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
              value={form.description}
              onChange={(e) => patch({ description: e.target.value })}
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
          {!managed && (
            <div className="flex items-center gap-2">
              <SaveButton
                isDirty={isDirty}
                isPending={saveMutation.isPending}
                status={saveStatus}
                onClick={() => saveMutation.mutate()}
                className="rounded border border-slate-400 px-3 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-50"
              >
                Save
              </SaveButton>
            </div>
          )}
        </>
      )}
    </div>
  );
}
