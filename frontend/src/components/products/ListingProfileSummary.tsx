import { useQuery } from "@tanstack/react-query";
import { listingProfilesApi, type ListingProfile } from "../../api/listingProfiles";
import { EBAY_CONDITION, ETSY_IS_SUPPLY, ETSY_WHEN_MADE, ETSY_WHO_MADE, type Option } from "../../lib/listingOptions";

/**
 * What choosing this listing profile commits a product to: the category, processing
 * profile, policies and making details the listing will go out with.
 *
 * Shown under the picker because a profile name alone ("3D printed home") says nothing
 * about the consequences of picking it, and the fields it carries — the category in
 * particular — are the ones a listing is hardest to correct after the fact. There is no
 * default profile to fall back on, so this is the moment the decision is made, and the
 * decision should be legible.
 *
 * Marketplace ids are resolved to the names they were chosen by, the same way the profile
 * editor shows them. An unresolvable id (lookup failed, or the marketplace no longer has
 * that object) is shown as the id rather than hidden, since "category #1234" is still more
 * honest than a blank.
 */
export function ListingProfileSummary({ profile }: { profile: ListingProfile }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs">
      {profile.platform === "etsy" ? <EtsyRows profile={profile} /> : <EbayRows profile={profile} />}
    </dl>
  );
}

function EtsyRows({ profile }: { profile: ListingProfile }) {
  const { data: taxonomy, isError: taxonomyFailed } = useQuery({
    queryKey: ["platforms", "etsy", "taxonomy", "node", profile.etsy_taxonomy_id],
    queryFn: () => listingProfilesApi.etsyTaxonomyNode(profile.etsy_taxonomy_id!),
    enabled: profile.etsy_taxonomy_id !== null,
    retry: false,
  });
  const { data: readinessStates, isError: readinessFailed } = useQuery({
    queryKey: ["platforms", "etsy", "readiness-states"],
    queryFn: () => listingProfilesApi.etsyReadinessStates(),
    enabled: profile.etsy_readiness_state_id !== null,
    retry: false,
  });
  const { data: returnPolicies, isError: returnsFailed } = useQuery({
    queryKey: ["platforms", "etsy", "return-policies"],
    queryFn: () => listingProfilesApi.etsyReturnPolicies(),
    enabled: profile.etsy_return_policy_id !== null,
    retry: false,
  });
  const { data: shippingProfiles, isError: shippingFailed } = useQuery({
    queryKey: ["platforms", "etsy", "shipping-profiles"],
    queryFn: () => listingProfilesApi.etsyShippingProfiles(),
    enabled: profile.etsy_shipping_profile_id !== null,
    retry: false,
  });

  return (
    <>
      <Row
        label="Category"
        value={remote(profile.etsy_taxonomy_id, taxonomy?.path, taxonomyFailed || !!taxonomy)}
      />
      <Row
        label="Processing"
        value={remote(profile.etsy_readiness_state_id, lookup(readinessStates, profile.etsy_readiness_state_id), readinessFailed || !!readinessStates)}
      />
      <Row label="Who made it" value={option(ETSY_WHO_MADE, profile.etsy_who_made)} />
      <Row label="When made" value={option(ETSY_WHEN_MADE, profile.etsy_when_made)} />
      <Row
        label="Listing is"
        value={option(ETSY_IS_SUPPLY, profile.etsy_is_supply === null ? null : String(profile.etsy_is_supply))}
      />
      <Row
        label="Returns"
        value={remote(profile.etsy_return_policy_id, lookup(returnPolicies, profile.etsy_return_policy_id), returnsFailed || !!returnPolicies)}
      />
      {/* Only a fallback: the product's own shipping profile wins when it is linked to
          Etsy, and the readiness report says which one applied. Listed so that a product
          with no linked shipping profile can see what it would ship under. */}
      {profile.etsy_shipping_profile_id !== null && (
        <Row
          label="Shipping (fallback)"
          value={remote(profile.etsy_shipping_profile_id, lookup(shippingProfiles, profile.etsy_shipping_profile_id), shippingFailed || !!shippingProfiles)}
        />
      )}
    </>
  );
}

function EbayRows({ profile }: { profile: ListingProfile }) {
  return (
    <>
      <Row label="Category" value={profile.ebay_category_id === null ? null : `#${profile.ebay_category_id}`} />
      <Row label="Condition" value={option(EBAY_CONDITION, profile.ebay_condition)} />
      <Row label="Payment policy" value={profile.ebay_payment_policy_id} />
      <Row label="Returns policy" value={profile.ebay_return_policy_id} />
      <Row label="Location" value={profile.ebay_merchant_location_key} />
      {profile.ebay_fulfillment_policy_id !== null && (
        <Row label="Postage (fallback)" value={profile.ebay_fulfillment_policy_id} />
      )}
    </>
  );
}

function Row({ label, value }: { label: string; value: string | null }) {
  return (
    <>
      <dt className="text-slate-500">{label}</dt>
      {/* A missing value is named as missing: it is what the readiness report will block
          on, and a blank cell reads as "nothing to say" rather than "nothing set". */}
      <dd className={value === null ? "italic text-amber-800" : "text-slate-800"}>{value ?? "Not set"}</dd>
    </>
  );
}

function option(options: Option[], value: string | null): string | null {
  if (value === null) return null;
  return options.find((o) => o.value === value)?.label ?? value;
}

function lookup(options: { id: string; label: string }[] | undefined, id: number | null): string | undefined {
  if (id === null || !options) return undefined;
  return options.find((o) => o.id === String(id))?.label;
}

/**
 * A marketplace id and the name it resolved to. `settled` means the lookup has answered
 * (with a result or an error) — until then the id shows with an ellipsis rather than
 * jumping from "#1234" to a name.
 */
function remote(id: number | null, label: string | undefined, settled: boolean): string | null {
  if (id === null) return null;
  if (label) return label;
  return settled ? `#${id}` : `#${id}…`;
}
