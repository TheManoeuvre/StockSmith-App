import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { platformsApi } from "../../api/platforms";
import { shippingProfilesApi } from "../../api/shippingProfiles";
import type { MarketplaceShippingProfile, ShippingProfile } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import {
  ReferenceDataTable,
  type ExpandedRowContext,
  type ReferenceField,
  type ReferenceRow,
} from "../reference/ReferenceDataTable";

type LinkedPlatform = "etsy" | "ebay";

const PLATFORM_LABEL: Record<LinkedPlatform, string> = { etsy: "Etsy", ebay: "eBay" };
const LINK_KEY: Record<LinkedPlatform, "etsy_shipping_profile_id" | "ebay_fulfillment_policy_id"> = {
  etsy: "etsy_shipping_profile_id",
  ebay: "ebay_fulfillment_policy_id",
};
const PRICE_KEY: Record<LinkedPlatform, "price_etsy" | "price_ebay"> = {
  etsy: "price_etsy",
  ebay: "price_ebay",
};

const money = (value: string | number | null | undefined) =>
  value == null || value === "" ? null : `£${Number(value).toFixed(2)}`;

/** What one marketplace's picker knows: its options, and why it might not have any. */
interface MarketplaceSource {
  connected: boolean;
  loaded: boolean;
  profiles: MarketplaceShippingProfile[];
  error: unknown;
}

function useMarketplaceSource(platform: LinkedPlatform): MarketplaceSource {
  const { data: status } = useQuery({
    queryKey: ["platforms", platform, "status"],
    queryFn: () => platformsApi.status(platform),
  });
  const connected = status?.connected ?? false;
  const { data, error } = useQuery({
    queryKey: ["shipping-profiles", "marketplace", platform],
    queryFn: () => shippingProfilesApi.marketplace(platform),
    enabled: connected,
    retry: false,
  });
  // Memoised so the field list built from it (and the row forms seeded from that) only
  // changes when something about the marketplace actually did.
  return useMemo(
    () => ({ connected, loaded: data !== undefined, profiles: data ?? [], error }),
    [connected, data, error]
  );
}

/** Which marketplace profile a row (as stored) is linked to, if the list has it. */
function linkedMarketplaceProfile(
  row: ShippingProfile,
  platform: LinkedPlatform,
  source: MarketplaceSource
): MarketplaceShippingProfile | undefined {
  const id = row[LINK_KEY[platform]];
  if (id == null) return undefined;
  return source.profiles.find((p) => p.id === String(id));
}

/**
 * Amber marker beside a per-channel price when the stored figure differs from what the
 * marketplace currently charges. Compared against the saved row, not the form — the form
 * is what the user is typing, and the marker is about what the app is running margin on.
 */
function DriftMarker({
  row,
  platform,
  source,
}: {
  row: ShippingProfile;
  platform: LinkedPlatform;
  source: MarketplaceSource;
}) {
  const remote = linkedMarketplaceProfile(row, platform, source);
  if (!remote) return null;
  if (remote.is_calculated) {
    return (
      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500" title="Postage is worked out per buyer at checkout on this marketplace, so there is no fixed price to import.">
        calculated on {PLATFORM_LABEL[platform]}
      </span>
    );
  }
  if (remote.domestic_price == null) return null;
  const stored = row[PRICE_KEY[platform]];
  const differs = stored == null || Number(stored) !== Number(remote.domestic_price);
  if (!differs) return null;
  return (
    <span
      className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800"
      title={`${PLATFORM_LABEL[platform]} currently charges ${money(remote.domestic_price)} for '${remote.title}'${remote.domestic_fallback ? " (first destination — no domestic one found)" : ""}. Pull the price to update margin.`}
    >
      {PLATFORM_LABEL[platform]} now {money(remote.domestic_price)}
    </span>
  );
}

/** The "Pull price from Etsy / eBay" actions and the marketplace's current view of the link. */
function MarketplaceLinkActions({
  ctx,
  sources,
}: {
  ctx: ExpandedRowContext<ShippingProfile>;
  sources: Record<LinkedPlatform, MarketplaceSource>;
}) {
  const importMutation = useMutation({
    mutationFn: (platform: LinkedPlatform) => shippingProfilesApi.importPrice(ctx.row.id, platform),
    onSuccess: (saved) => ctx.acceptSaved(saved),
  });

  const platforms = (["etsy", "ebay"] as const).filter((p) => ctx.row[LINK_KEY[p]] != null);
  if (platforms.length === 0) return null;

  return (
    <div className="flex flex-col gap-2 rounded border border-slate-200 bg-white p-2 text-sm">
      <span className="text-xs font-medium text-slate-500">Marketplace link</span>
      {platforms.map((platform) => {
        const source = sources[platform];
        const remote = linkedMarketplaceProfile(ctx.row, platform, source);
        const storedLink = String(ctx.row[LINK_KEY[platform]]);
        const formLink = ctx.form[LINK_KEY[platform]] ?? "";
        const unsavedLinkChange = formLink !== storedLink;
        const calculated = remote?.is_calculated ?? false;
        return (
          <div key={platform} className="flex flex-wrap items-center gap-2">
            <span className="text-slate-600">
              {PLATFORM_LABEL[platform]}:{" "}
              {remote ? (
                <>
                  <span className="font-medium">{remote.title}</span>
                  {calculated ? (
                    <span className="ml-1 text-slate-400">— calculated on {PLATFORM_LABEL[platform]}</span>
                  ) : remote.domestic_price != null ? (
                    <span className="ml-1 text-slate-400">
                      — charges {money(remote.domestic_price)}
                      {remote.domestic_fallback && " (first destination; no domestic one found)"}
                    </span>
                  ) : null}
                </>
              ) : source.connected && source.loaded ? (
                <span className="text-red-700">
                  linked profile {storedLink} is not in {PLATFORM_LABEL[platform]}'s current list
                </span>
              ) : (
                <span className="text-slate-400">id {storedLink}</span>
              )}
            </span>
            <button
              type="button"
              className="rounded border border-slate-300 px-2 py-0.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!source.connected || calculated || unsavedLinkChange || importMutation.isPending}
              title={
                !source.connected
                  ? `${PLATFORM_LABEL[platform]} is not connected`
                  : calculated
                    ? "Calculated profiles have no fixed price to pull"
                    : unsavedLinkChange
                      ? "Save the new link first"
                      : `Write what ${PLATFORM_LABEL[platform]} charges the buyer into the ${PLATFORM_LABEL[platform]} price`
              }
              onClick={() => importMutation.mutate(platform)}
            >
              Pull price from {PLATFORM_LABEL[platform]}
            </button>
          </div>
        );
      })}
      <ErrorBanner error={importMutation.error} />
    </div>
  );
}

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
 * A profile can be linked to one Etsy shipping profile and/or one eBay postage policy. The
 * link makes the marketplace the source of truth for the price charged to the buyer on that
 * channel (pulled here on demand, refreshed on the sync schedule) and makes this profile the
 * place a draft listing takes its marketplace shipping id from. Linking alone changes no
 * number — the pull is a separate, visible act.
 */
export function ShippingProfileSettings() {
  const queryClient = useQueryClient();
  const [showArchived, setShowArchived] = useState(false);

  const queryKey = ["settings", "shipping-profiles", showArchived] as const;
  const { data: profiles } = useQuery({
    queryKey,
    queryFn: () => shippingProfilesApi.list(showArchived),
  });

  const etsy = useMarketplaceSource("etsy");
  const ebay = useMarketplaceSource("ebay");
  const sources = useMemo(() => ({ etsy, ebay }), [etsy, ebay]);

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

  const fields = useMemo<ReferenceField[]>(() => {
    const linkField = (platform: LinkedPlatform, label: string): ReferenceField => {
      const source = sources[platform];
      const hint = !source.connected
        ? `Connect ${PLATFORM_LABEL[platform]} in Integrations to link a profile.`
        : source.error
          ? `Couldn't read ${PLATFORM_LABEL[platform]}'s profiles — reconnect ${PLATFORM_LABEL[platform]} to grant the newer permission.`
          : source.loaded && source.profiles.length === 0
            ? `No ${label.toLowerCase()}s on this ${PLATFORM_LABEL[platform]} account.`
            : undefined;
      return {
        key: LINK_KEY[platform],
        label,
        type: "select",
        placeholder: "Not linked",
        disabled: !source.connected,
        hint,
        options: source.profiles.map((p) => ({
          value: p.id,
          label: p.is_calculated
            ? `${p.title} (calculated on ${PLATFORM_LABEL[platform]})`
            : p.domestic_price != null
              ? `${p.title} (${money(p.domestic_price)})`
              : p.title,
        })),
      };
    };
    const channelPrice = (platform: LinkedPlatform): ReferenceField => ({
      key: PRICE_KEY[platform],
      label: `Price charged (${PLATFORM_LABEL[platform]})`,
      type: "money",
      nullable: true,
      // Blank means "same as the default price", so show that figure where the value would go.
      placeholder: (row: ReferenceRow) => (row as ShippingProfile).price,
      adornment: (row: ReferenceRow) => (
        <DriftMarker row={row as ShippingProfile} platform={platform} source={sources[platform]} />
      ),
    });
    return [
      { key: "name", label: "Name" },
      { key: "price", label: "Price charged (default)", type: "money" },
      channelPrice("etsy"),
      channelPrice("ebay"),
      { key: "cost_etsy", label: "Cost (Etsy)", type: "money" },
      { key: "cost_ebay", label: "Cost (eBay)", type: "money" },
      { key: "cost_manual", label: "Cost (manual)", type: "money" },
      linkField("etsy", "Etsy shipping profile"),
      linkField("ebay", "eBay postage policy"),
    ];
  }, [sources]);

  return (
    <div className="flex flex-col gap-2">
      <ReferenceDataTable<ShippingProfile>
        title="Shipping profiles"
        description="What you charge for postage, and what it actually costs you per channel. Products default to one; orders snapshot the cost for their own channel when they ship, so historical profit doesn't drift. Link a profile to its Etsy shipping profile or eBay postage policy to pull the price the marketplace actually charges, and to give draft listings their shipping settings."
        segment="shipping-profiles"
        queryKey={queryKey}
        api={{
          list: () => shippingProfilesApi.list(showArchived),
          create: (name: string) => shippingProfilesApi.create({ name }),
          update: shippingProfilesApi.update,
          remove: shippingProfilesApi.remove,
          merge: shippingProfilesApi.merge,
        }}
        fields={fields}
        usageLabel={(n) => `${n} record${n === 1 ? "" : "s"}`}
        expandedExtras={(ctx) => <MarketplaceLinkActions ctx={ctx} sources={sources} />}
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
