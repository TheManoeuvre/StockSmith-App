import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { listingProfilesApi, type ListingProfile, type ListingProfileWrite } from "../../api/listingProfiles";
import type { ListingPlatform } from "../../api/types";
import {
  EBAY_CONDITION,
  ETSY_IS_SUPPLY,
  ETSY_WHEN_MADE,
  ETSY_WHO_MADE,
  type Option,
} from "../../lib/listingOptions";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { ErrorBanner } from "../common/ErrorBanner";
import { TaxonomyPicker } from "./TaxonomyPicker";

/**
 * Named bundles of the marketplace metadata a listing needs — category, policies, who made
 * it — with one marked as the default.
 *
 * Bundles rather than per-product fields because products that differ tend to differ
 * together: a different category usually arrives with a different shipping profile and
 * processing time. Most products answer identically, so most shops need one profile.
 *
 * Every field that a marketplace identifies by numeric id is chosen by name here. Etsy in
 * particular surfaces none of those ids in its own seller UI, so a form asking for them
 * directly was asking the user to go and read an API response.
 */
export function ListingProfiles({ platform }: { platform: ListingPlatform }) {
  const queryClient = useQueryClient();
  const [editingId, setEditingId] = useState<number | "new" | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<ListingProfile | null>(null);

  const { data: profiles, error } = useQuery({
    queryKey: ["settings", "listing-profiles", platform],
    queryFn: () => listingProfilesApi.list(platform),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["settings", "listing-profiles", platform] });
    // Readiness for every product depends on these, so it all goes stale at once.
    queryClient.invalidateQueries({ queryKey: ["platforms", platform] });
  };

  const deleteMutation = useMutation({
    mutationFn: (id: number) => listingProfilesApi.remove(platform, id),
    onSuccess: () => {
      setConfirmDelete(null);
      invalidate();
    },
  });

  return (
    <div className="flex flex-col gap-2 rounded border border-slate-200 bg-white p-3 text-sm">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-medium">{PLATFORM_LABELS[platform]} listing profiles</p>
          <p className="text-xs text-slate-500">
            The category, policies and making details a new listing needs. Products use the default unless
            you give them their own.
          </p>
        </div>
        <button
          onClick={() => setEditingId("new")}
          className="shrink-0 rounded border border-slate-300 px-3 py-1.5"
        >
          New profile
        </button>
      </div>

      <ErrorBanner error={error} />
      <ErrorBanner error={deleteMutation.error} />

      {profiles?.length === 0 && (
        <p className="text-slate-600">
          No profiles yet. Products can't be drafted to {PLATFORM_LABELS[platform]} until one exists.
        </p>
      )}

      {profiles?.map((profile) =>
        editingId === profile.id ? (
          <ProfileForm
            key={profile.id}
            platform={platform}
            profile={profile}
            onDone={() => {
              setEditingId(null);
              invalidate();
            }}
          />
        ) : (
          <div key={profile.id} className="flex items-center justify-between rounded border border-slate-200 p-2">
            <span>
              {profile.name}
              {profile.is_default && (
                <span className="ml-2 rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">Default</span>
              )}
            </span>
            <span className="flex gap-3 text-xs">
              <button onClick={() => setEditingId(profile.id)} className="text-slate-600 underline">
                Edit
              </button>
              <button onClick={() => setConfirmDelete(profile)} className="text-slate-600 underline">
                Delete
              </button>
            </span>
          </div>
        )
      )}

      {editingId === "new" && (
        <ProfileForm
          platform={platform}
          onDone={() => {
            setEditingId(null);
            invalidate();
          }}
        />
      )}

      {confirmDelete && (
        <ConfirmDialog
          open
          title={`Delete "${confirmDelete.name}"?`}
          body="Products using it fall back to the default profile. Their listing copy is kept."
          confirmLabel="Delete"
          busy={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(confirmDelete.id)}
          onCancel={() => setConfirmDelete(null)}
        />
      )}
    </div>
  );
}

function ProfileForm({
  platform,
  profile,
  onDone,
}: {
  platform: ListingPlatform;
  profile?: ListingProfile;
  onDone: () => void;
}) {
  const [draft, setDraft] = useState<ListingProfileWrite>({
    name: profile?.name ?? "",
    is_default: profile?.is_default ?? false,
    etsy_taxonomy_id: profile?.etsy_taxonomy_id ?? null,
    etsy_who_made: profile?.etsy_who_made ?? null,
    etsy_when_made: profile?.etsy_when_made ?? null,
    etsy_is_supply: profile?.etsy_is_supply ?? null,
    etsy_shipping_profile_id: profile?.etsy_shipping_profile_id ?? null,
    etsy_return_policy_id: profile?.etsy_return_policy_id ?? null,
    etsy_processing_min: profile?.etsy_processing_min ?? null,
    etsy_processing_max: profile?.etsy_processing_max ?? null,
    ebay_category_id: profile?.ebay_category_id ?? null,
    ebay_condition: profile?.ebay_condition ?? null,
    ebay_fulfillment_policy_id: profile?.ebay_fulfillment_policy_id ?? null,
    ebay_payment_policy_id: profile?.ebay_payment_policy_id ?? null,
    ebay_return_policy_id: profile?.ebay_return_policy_id ?? null,
    ebay_merchant_location_key: profile?.ebay_merchant_location_key ?? null,
    ebay_marketplace_id: profile?.ebay_marketplace_id ?? null,
  });

  // What's stored is an id; this is the name it was chosen by, so re-opening a saved
  // profile shows a category rather than a number.
  const [taxonomyPath, setTaxonomyPath] = useState<string | null>(null);

  const { data: resolvedNode } = useQuery({
    queryKey: ["platforms", "etsy", "taxonomy", "node", profile?.etsy_taxonomy_id],
    queryFn: () => listingProfilesApi.etsyTaxonomyNode(profile!.etsy_taxonomy_id!),
    enabled: platform === "etsy" && !!profile?.etsy_taxonomy_id,
    retry: false,
  });
  useEffect(() => {
    if (resolvedNode && taxonomyPath === null) setTaxonomyPath(resolvedNode.path);
  }, [resolvedNode, taxonomyPath]);

  const { data: shippingProfiles, error: shippingError } = useQuery({
    queryKey: ["platforms", "etsy", "shipping-profiles"],
    queryFn: () => listingProfilesApi.etsyShippingProfiles(),
    enabled: platform === "etsy",
    retry: false,
  });
  const { data: returnPolicies } = useQuery({
    queryKey: ["platforms", "etsy", "return-policies"],
    queryFn: () => listingProfilesApi.etsyReturnPolicies(),
    enabled: platform === "etsy",
    retry: false,
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      profile
        ? listingProfilesApi.update(platform, profile.id, draft)
        : listingProfilesApi.create(platform, draft),
    onSuccess: onDone,
  });

  const set = (patch: ListingProfileWrite) => setDraft((d) => ({ ...d, ...patch }));
  const text = (value: string) => (value.trim() === "" ? null : value);

  return (
    <div className="flex flex-col gap-2 rounded border border-slate-300 bg-slate-50 p-3">
      <label className="flex flex-col gap-1 text-xs">
        <span>Profile name</span>
        <input
          className="rounded border border-slate-300 px-2 py-1"
          value={draft.name ?? ""}
          onChange={(e) => set({ name: e.target.value })}
          placeholder="e.g. 3D printed home accessory"
        />
      </label>

      {platform === "etsy" ? (
        <div className="grid grid-cols-2 gap-2">
          <Field label="Category" required>
            <TaxonomyPicker
              value={draft.etsy_taxonomy_id ?? null}
              valueLabel={taxonomyPath}
              onChange={(id, path) => {
                set({ etsy_taxonomy_id: id });
                setTaxonomyPath(path);
              }}
            />
          </Field>
          <Field label="Shipping profile" required>
            <RemoteSelect
              options={shippingProfiles}
              failed={!!shippingError}
              value={draft.etsy_shipping_profile_id ?? null}
              onChange={(v) => set({ etsy_shipping_profile_id: v === null ? null : Number(v) })}
              emptyHint="No shipping profiles on this Etsy shop."
              failedHint="Couldn't read your shipping profiles. Reconnect Etsy in Settings — this needs a permission that older connections didn't grant."
            />
          </Field>
          <Field label="Who made it" required>
            <OptionSelect
              options={ETSY_WHO_MADE}
              value={draft.etsy_who_made ?? null}
              onChange={(v) => set({ etsy_who_made: v })}
            />
          </Field>
          <Field label="When was it made" required>
            <OptionSelect
              options={ETSY_WHEN_MADE}
              value={draft.etsy_when_made ?? null}
              onChange={(v) => set({ etsy_when_made: v })}
            />
          </Field>
          <Field label="This listing is">
            <OptionSelect
              options={ETSY_IS_SUPPLY}
              value={
                draft.etsy_is_supply === null || draft.etsy_is_supply === undefined
                  ? null
                  : String(draft.etsy_is_supply)
              }
              onChange={(v) => set({ etsy_is_supply: v === null ? null : v === "true" })}
            />
          </Field>
          <Field label="Return policy">
            <RemoteSelect
              options={returnPolicies}
              value={draft.etsy_return_policy_id ?? null}
              onChange={(v) => set({ etsy_return_policy_id: v === null ? null : Number(v) })}
              emptyHint="No return policies set up on Etsy."
            />
          </Field>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <Field label="Category id" required>
            <input
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={draft.ebay_category_id ?? ""}
              onChange={(e) => set({ ebay_category_id: text(e.target.value) })}
            />
          </Field>
          <Field label="Condition" required>
            <OptionSelect
              options={EBAY_CONDITION}
              value={draft.ebay_condition ?? null}
              onChange={(v) => set({ ebay_condition: v })}
            />
          </Field>
          <Field label="Postage policy id" required>
            <input
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={draft.ebay_fulfillment_policy_id ?? ""}
              onChange={(e) => set({ ebay_fulfillment_policy_id: text(e.target.value) })}
            />
          </Field>
          <Field label="Payment policy id" required>
            <input
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={draft.ebay_payment_policy_id ?? ""}
              onChange={(e) => set({ ebay_payment_policy_id: text(e.target.value) })}
            />
          </Field>
          <Field label="Returns policy id" required>
            <input
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={draft.ebay_return_policy_id ?? ""}
              onChange={(e) => set({ ebay_return_policy_id: text(e.target.value) })}
            />
          </Field>
          <Field label="Location key" required>
            <input
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={draft.ebay_merchant_location_key ?? ""}
              onChange={(e) => set({ ebay_merchant_location_key: text(e.target.value) })}
            />
          </Field>
        </div>
      )}

      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={draft.is_default ?? false}
          onChange={(e) => set({ is_default: e.target.checked })}
        />
        <span>Use this profile by default</span>
      </label>

      <ErrorBanner error={saveMutation.error} />
      <div className="flex gap-2">
        <button
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending || !draft.name?.trim()}
          className="rounded border border-slate-400 px-3 py-1 text-xs disabled:opacity-50"
        >
          Save
        </button>
        <button onClick={onDone} className="rounded border border-slate-300 px-3 py-1 text-xs">
          Cancel
        </button>
      </div>
    </div>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs">
      <span>
        {label}
        {/* Marked from the marketplace's own schema — these are the fields the create call
            is refused without, not a house preference. */}
        {required && <span className="ml-1 text-red-600">*</span>}
      </span>
      {children}
    </label>
  );
}

/** A select over a fixed vocabulary, showing labels and storing the marketplace's value. */
function OptionSelect({
  options,
  value,
  onChange,
}: {
  options: Option[];
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  return (
    <select
      className="w-full rounded border border-slate-300 px-2 py-1"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
    >
      <option value="">—</option>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

/**
 * A select over options fetched from the marketplace.
 *
 * `failed` is kept separate from an empty list on purpose: "you have none" and "we weren't
 * allowed to ask" look identical in the data and need completely different actions from the
 * user. Collapsing them is how someone ends up creating a shipping profile they already had.
 */
function RemoteSelect({
  options,
  value,
  onChange,
  emptyHint,
  failed,
  failedHint,
}: {
  options: { id: string; label: string }[] | undefined;
  value: number | null;
  onChange: (value: string | null) => void;
  emptyHint: string;
  failed?: boolean;
  failedHint?: string;
}) {
  if (failed) return <p className="text-xs text-amber-800">{failedHint ?? emptyHint}</p>;
  if (options && options.length === 0) return <p className="text-xs text-amber-800">{emptyHint}</p>;
  return (
    <select
      className="w-full rounded border border-slate-300 px-2 py-1"
      value={value === null ? "" : String(value)}
      onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
      disabled={!options}
    >
      <option value="">{options ? "—" : "Loading…"}</option>
      {options?.map((option) => (
        <option key={option.id} value={option.id}>
          {option.label}
        </option>
      ))}
    </select>
  );
}
