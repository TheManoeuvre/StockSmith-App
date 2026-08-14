import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  ETSY_WHEN_MADE,
  ETSY_WHO_MADE,
  listingProfilesApi,
  type ListingProfile,
  type ListingProfileWrite,
} from "../../api/listingProfiles";
import type { ListingPlatform } from "../../api/types";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { ErrorBanner } from "../common/ErrorBanner";

/**
 * Named bundles of the marketplace metadata a listing needs — category, policies, who
 * made it — with one marked as the default.
 *
 * Bundles rather than per-product fields because products that differ tend to differ
 * together: a different category usually arrives with a different shipping profile and
 * processing time. Most products answer identically, so most shops need one profile.
 *
 * Nothing is pre-filled. A wrong policy id doesn't fail loudly, it silently mis-ships, so
 * an empty field that blocks a draft with a clear message beats a plausible guess.
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

  const saveMutation = useMutation({
    mutationFn: () =>
      profile
        ? listingProfilesApi.update(platform, profile.id, draft)
        : listingProfilesApi.create(platform, draft),
    onSuccess: onDone,
  });

  const set = (patch: ListingProfileWrite) => setDraft((d) => ({ ...d, ...patch }));
  const num = (value: string) => (value.trim() === "" ? null : Number(value));
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
          <Field label="Category (taxonomy id)" required>
            <input
              type="number"
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={draft.etsy_taxonomy_id ?? ""}
              onChange={(e) => set({ etsy_taxonomy_id: num(e.target.value) })}
            />
          </Field>
          <Field label="Etsy shipping profile id" required>
            <input
              type="number"
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={draft.etsy_shipping_profile_id ?? ""}
              onChange={(e) => set({ etsy_shipping_profile_id: num(e.target.value) })}
            />
          </Field>
          <Field label="Who made it" required>
            <select
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={draft.etsy_who_made ?? ""}
              onChange={(e) => set({ etsy_who_made: text(e.target.value) })}
            >
              <option value="">—</option>
              {ETSY_WHO_MADE.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </Field>
          <Field label="When made" required>
            <select
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={draft.etsy_when_made ?? ""}
              onChange={(e) => set({ etsy_when_made: text(e.target.value) })}
            >
              <option value="">—</option>
              {ETSY_WHEN_MADE.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Is a supply">
            <select
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={draft.etsy_is_supply === null || draft.etsy_is_supply === undefined ? "" : String(draft.etsy_is_supply)}
              onChange={(e) =>
                set({ etsy_is_supply: e.target.value === "" ? null : e.target.value === "true" })
              }
            >
              <option value="">—</option>
              <option value="false">Finished product</option>
              <option value="true">Supply</option>
            </select>
          </Field>
          <Field label="Return policy id">
            <input
              type="number"
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={draft.etsy_return_policy_id ?? ""}
              onChange={(e) => set({ etsy_return_policy_id: num(e.target.value) })}
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
            <input
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={draft.ebay_condition ?? ""}
              onChange={(e) => set({ ebay_condition: text(e.target.value) })}
              placeholder="NEW"
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
