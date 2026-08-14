import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { etsyProfileProposalsApi, type ProfileProposal } from "../../api/etsyBackfill";
import { ErrorBanner } from "../common/ErrorBanner";

/**
 * Suggests listing profiles from the metadata already on your Etsy listings.
 *
 * A shop's catalogue spans a handful of genuine combinations, not one per product, so
 * this groups the matched listings and proposes one profile per combination. Setting
 * profiles up becomes reviewing two or three suggestions instead of looking nine fields
 * up in another window.
 *
 * Behind a button for the same reason as the value backfill: it crawls the shop.
 */
export function EtsyProfileProposalsPanel() {
  const queryClient = useQueryClient();
  const [names, setNames] = useState<Record<number, string>>({});
  const [accepted, setAccepted] = useState<Set<number>>(new Set());

  const previewMutation = useMutation({
    mutationFn: () => etsyProfileProposalsApi.preview(),
    onSuccess: (data) => {
      setNames(Object.fromEntries(data.proposals.map((p) => [p.index, p.suggested_name])));
      // Only complete combinations are pre-accepted. An incomplete one is still worth
      // showing — it tells you which field to go and set — but creating it would produce a
      // profile that can't draft anything.
      setAccepted(new Set(data.proposals.filter((p) => p.is_complete).map((p) => p.index)));
    },
  });

  const applyMutation = useMutation({
    mutationFn: () =>
      etsyProfileProposalsApi.apply(
        [...accepted].map((index) => ({ index, name: names[index] ?? "" }))
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings", "listing-profiles", "etsy"] });
      queryClient.invalidateQueries({ queryKey: ["platforms", "etsy"] });
      previewMutation.mutate();
    },
  });

  const proposals = previewMutation.data?.proposals ?? [];

  function toggle(index: number) {
    setAccepted((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  return (
    <div className="flex flex-col gap-2 rounded border border-slate-200 bg-white p-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="font-medium">Suggest profiles from Etsy</p>
          <p className="text-xs text-slate-500">
            Reads the category, policies and making details off your existing listings and groups them
            into profiles.
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

      {previewMutation.data && proposals.length === 0 && (
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

      {proposals.length > 0 && (
        <button
          onClick={() => applyMutation.mutate()}
          disabled={applyMutation.isPending || accepted.size === 0}
          className="self-start rounded border border-slate-400 px-3 py-1.5 disabled:opacity-50"
        >
          {applyMutation.isPending ? "Creating…" : `Create ${accepted.size} profile(s)`}
        </button>
      )}

      {applyMutation.data && (
        <div className="rounded bg-slate-50 p-2">
          Created <strong>{applyMutation.data.profiles_created}</strong> profile(s) and assigned{" "}
          <strong>{applyMutation.data.products_assigned}</strong> product(s).
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
        {proposal.shipping_profile_id !== null && <> · shipping {proposal.shipping_profile_id}</>}
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
