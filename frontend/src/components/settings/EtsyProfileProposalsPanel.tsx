import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  etsyProfileProposalsApi,
  type ProfileProposal,
  type ShippingProfileProposal,
  type ShippingProfileSelection,
} from "../../api/etsyBackfill";
import { shippingProfilesApi } from "../../api/shippingProfiles";
import { ErrorBanner } from "../common/ErrorBanner";

/**
 * Suggests listing profiles from the metadata already on your Etsy listings.
 *
 * A shop's catalogue spans a handful of genuine combinations, not one per product, so
 * this groups the matched listings and proposes one profile per combination. Setting
 * profiles up becomes reviewing two or three suggestions instead of looking nine fields
 * up in another window.
 *
 * Shipping is a separate group. The draft takes its Etsy shipping profile from the
 * product's own StockSmith shipping profile (linked to Etsy), not from the listing
 * profile, so each Etsy shipping profile in use that no local profile is linked to yet is
 * offered on its own: create a local profile for it, or link one you already have.
 *
 * Behind a button for the same reason as the value backfill: it crawls the shop.
 */
export function EtsyProfileProposalsPanel() {
  const queryClient = useQueryClient();
  const [names, setNames] = useState<Record<number, string>>({});
  const [accepted, setAccepted] = useState<Set<number>>(new Set());
  // Keyed by Etsy shipping profile id. A link is the local profile to link instead of
  // creating one; null means create.
  const [shippingNames, setShippingNames] = useState<Record<number, string>>({});
  const [shippingLinks, setShippingLinks] = useState<Record<number, number | null>>({});
  const [shippingAccepted, setShippingAccepted] = useState<Set<number>>(new Set());

  const previewMutation = useMutation({
    mutationFn: () => etsyProfileProposalsApi.preview(),
    onSuccess: (data) => {
      setNames(Object.fromEntries(data.proposals.map((p) => [p.index, p.suggested_name])));
      // Only complete combinations are pre-accepted. An incomplete one is still worth
      // showing — it tells you which field to go and set — but creating it would produce a
      // profile that can't draft anything.
      setAccepted(new Set(data.proposals.filter((p) => p.is_complete).map((p) => p.index)));
      const shipping = data.shipping_profiles ?? [];
      setShippingNames(Object.fromEntries(shipping.map((p) => [p.etsy_shipping_profile_id, p.title])));
      setShippingLinks({});
      setShippingAccepted(new Set(shipping.map((p) => p.etsy_shipping_profile_id)));
    },
  });

  const proposals = previewMutation.data?.proposals ?? [];
  const shippingProposals = previewMutation.data?.shipping_profiles ?? [];

  // Local profiles not yet linked to Etsy, offered as "link this one instead".
  const localProfiles = useQuery({
    queryKey: ["settings", "shipping-profiles"],
    queryFn: () => shippingProfilesApi.list(),
    enabled: shippingProposals.length > 0,
  });
  const linkable = (localProfiles.data ?? []).filter((p) => p.etsy_shipping_profile_id == null);

  const applyMutation = useMutation({
    mutationFn: () =>
      etsyProfileProposalsApi.apply(
        [...accepted].map((index) => ({ index, name: names[index] ?? "" })),
        [...shippingAccepted].map((id): ShippingProfileSelection => {
          const link = shippingLinks[id];
          return link != null
            ? { etsy_shipping_profile_id: id, link_shipping_profile_id: link }
            : { etsy_shipping_profile_id: id, name: shippingNames[id] ?? "" };
        })
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings", "listing-profiles", "etsy"] });
      queryClient.invalidateQueries({ queryKey: ["settings", "shipping-profiles"] });
      queryClient.invalidateQueries({ queryKey: ["platforms", "etsy"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      previewMutation.mutate();
    },
  });

  function toggle(index: number) {
    setAccepted((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  function toggleShipping(id: number) {
    setShippingAccepted((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const acceptedCount = accepted.size + shippingAccepted.size;
  const result = applyMutation.data;

  return (
    <div className="flex flex-col gap-2 rounded border border-slate-200 bg-white p-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="font-medium">Suggest profiles from Etsy</p>
          <p className="text-xs text-slate-500">
            Reads the category, policies and making details off your existing listings and groups them
            into profiles, and offers a shipping profile for each Etsy one you use.
          </p>
        </div>
        <button
          onClick={() => previewMutation.mutate()}
          disabled={previewMutation.isPending}
          className="shrink-0 rounded border border-slate-300 px-3 py-1.5 disabled:opacity-50"
        >
          {previewMutation.isPending ? "Reading…" : "Suggest profiles"}
        </button>
      </div>

      <ErrorBanner error={previewMutation.error} />
      <ErrorBanner error={applyMutation.error} />

      {previewMutation.data && proposals.length === 0 && shippingProposals.length === 0 && (
        <p className="text-slate-600">
          Nothing to suggest — no linked Etsy listing carries the details a profile needs.
        </p>
      )}

      {proposals.map((proposal) => (
        <ProposalRow
          key={proposal.index}
          proposal={proposal}
          name={names[proposal.index] ?? ""}
          accepted={accepted.has(proposal.index)}
          onToggle={() => toggle(proposal.index)}
          onRename={(value) => setNames((n) => ({ ...n, [proposal.index]: value }))}
        />
      ))}

      {shippingProposals.length > 0 && (
        <div className="mt-1 flex flex-col gap-2">
          <div>
            <p className="font-medium">Shipping profiles</p>
            <p className="text-xs text-slate-500">
              Etsy shipping profiles your listings use that no StockSmith shipping profile is linked to.
              Drafts take their Etsy shipping profile from the product's shipping profile, so each needs
              a local one — new, or an existing one linked to it.
            </p>
          </div>
          {shippingProposals.map((proposal) => (
            <ShippingProposalRow
              key={proposal.etsy_shipping_profile_id}
              proposal={proposal}
              name={shippingNames[proposal.etsy_shipping_profile_id] ?? ""}
              link={shippingLinks[proposal.etsy_shipping_profile_id] ?? null}
              linkable={linkable}
              accepted={shippingAccepted.has(proposal.etsy_shipping_profile_id)}
              onToggle={() => toggleShipping(proposal.etsy_shipping_profile_id)}
              onRename={(value) =>
                setShippingNames((n) => ({ ...n, [proposal.etsy_shipping_profile_id]: value }))
              }
              onLink={(value) =>
                setShippingLinks((l) => ({ ...l, [proposal.etsy_shipping_profile_id]: value }))
              }
            />
          ))}
        </div>
      )}

      {(proposals.length > 0 || shippingProposals.length > 0) && (
        <button
          onClick={() => applyMutation.mutate()}
          disabled={applyMutation.isPending || acceptedCount === 0}
          className="self-start rounded border border-slate-400 px-3 py-1.5 disabled:opacity-50"
        >
          {applyMutation.isPending ? "Creating…" : `Create ${acceptedCount} profile(s)`}
        </button>
      )}

      {result && (
        <div className="rounded bg-slate-50 p-2">
          Created <strong>{result.profiles_created}</strong> profile(s) and assigned{" "}
          <strong>{result.products_assigned}</strong> product(s).
          {(result.shipping_profiles_created > 0 || result.shipping_profiles_linked > 0) && (
            <>
              {" "}
              Shipping: created <strong>{result.shipping_profiles_created}</strong>, linked{" "}
              <strong>{result.shipping_profiles_linked}</strong>, assigned{" "}
              <strong>{result.shipping_products_assigned}</strong> product(s).
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ProposalRow({
  proposal,
  name,
  accepted,
  onToggle,
  onRename,
}: {
  proposal: ProfileProposal;
  name: string;
  accepted: boolean;
  onToggle: () => void;
  onRename: (value: string) => void;
}) {
  return (
    <div className={`rounded border p-2 ${proposal.is_complete ? "border-slate-200" : "border-amber-300"}`}>
      <div className="flex items-center gap-2">
        <input type="checkbox" checked={accepted} onChange={onToggle} />
        <input
          className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
          value={name}
          onChange={(e) => onRename(e.target.value)}
        />
        <span className="shrink-0 text-xs text-slate-500">{proposal.product_count} product(s)</span>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        {proposal.taxonomy_id !== null && <>Category {proposal.taxonomy_id} · </>}
        {proposal.who_made} · {proposal.when_made}
      </p>
      <p className="text-xs text-slate-500">{proposal.product_names.join(", ")}</p>
      {!proposal.is_complete && (
        <p className="mt-1 text-xs text-amber-800">
          Missing something Etsy requires — you'll need to fill it in before drafting with this.
        </p>
      )}
    </div>
  );
}

function ShippingProposalRow({
  proposal,
  name,
  link,
  linkable,
  accepted,
  onToggle,
  onRename,
  onLink,
}: {
  proposal: ShippingProfileProposal;
  name: string;
  link: number | null;
  linkable: { id: number; name: string }[];
  accepted: boolean;
  onToggle: () => void;
  onRename: (value: string) => void;
  onLink: (value: number | null) => void;
}) {
  const price = proposal.is_calculated
    ? "calculated on Etsy"
    : proposal.domestic_price != null
      ? `buyer pays ${proposal.domestic_price}`
      : "no fixed price";
  return (
    <div className="rounded border border-slate-200 p-2">
      <div className="flex items-center gap-2">
        <input type="checkbox" checked={accepted} onChange={onToggle} aria-label={`Accept ${proposal.title}`} />
        {link == null ? (
          <input
            className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
            value={name}
            onChange={(e) => onRename(e.target.value)}
            aria-label={`Name for ${proposal.title}`}
          />
        ) : (
          <span className="flex-1 text-sm">{proposal.title}</span>
        )}
        <select
          className="rounded border border-slate-300 px-2 py-1 text-xs"
          value={link ?? ""}
          onChange={(e) => onLink(e.target.value === "" ? null : Number(e.target.value))}
          aria-label={`Local profile for ${proposal.title}`}
        >
          <option value="">Create new</option>
          {linkable.map((p) => (
            <option key={p.id} value={p.id}>
              Link: {p.name}
            </option>
          ))}
        </select>
        <span className="shrink-0 text-xs text-slate-500">{proposal.product_count} product(s)</span>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        Etsy shipping profile {proposal.etsy_shipping_profile_id} · {price}
      </p>
      <p className="text-xs text-slate-500">{proposal.product_names.join(", ")}</p>
    </div>
  );
}
