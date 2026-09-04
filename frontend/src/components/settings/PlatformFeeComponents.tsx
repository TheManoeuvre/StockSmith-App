import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { feeConfigApi, type FeeBasis, type PlatformFeeComponent } from "../../api/feeConfig";
import type { ListingPlatform } from "../../api/types";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { PLATFORM_LABELS } from "../../lib/platforms";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveButton } from "../common/SaveButton";

interface NewComponentForm {
  name: string;
  basis: FeeBasis;
  rate: string;
  fixed: string;
}

const EMPTY_COMPONENT: NewComponentForm = { name: "", basis: "sale_price_plus_shipping", rate: "", fixed: "" };

/**
 * One platform's fee breakdown, shown on the Pricing & fees page — once per connectable
 * platform, next to the other one, since a shop pricing a product wants to compare Etsy's
 * and eBay's fee structure side by side rather than hunting for it on each platform's own
 * Stores & sync accordion.
 *
 * The fee *source* (which of these breakdowns is applied shop-wide) is a separate, genuinely
 * global setting — see MarginFeeSettings — and is not per-platform at all despite naming one.
 *
 * Components are applied in display_order, each one adding to a running subtotal — which is how
 * "VAT on fees" is modelled: a fees_subtotal-basis component multiplies what came before it.
 * See services/platform_fees.py compute_effective_fee_amount.
 */
export const BASIS_LABELS: Record<FeeBasis, string> = {
  sale_price: "% of sale price",
  sale_price_plus_shipping: "% of sale price + shipping",
  fees_subtotal: "% of fees so far (e.g. VAT)",
};

export function PlatformFeeComponents({ platform }: { platform: ListingPlatform }) {
  const queryClient = useQueryClient();
  const { data: components } = useQuery({
    queryKey: ["settings", "platform-fee-components", platform],
    queryFn: () => feeConfigApi.listFeeComponents(platform),
  });

  // A command form rather than an editor: it has no server state to mirror, so it seeds from a
  // constant and is registered purely so half-typed input isn't silently dropped on navigate.
  const {
    value: draft,
    setValue: setDraft,
    isDirty: draftDirty,
    markSaved: clearDraft,
  } = useEditableCopy<NewComponentForm>({
    key: `fee-components/${platform}/new`,
    label: `New ${PLATFORM_LABELS[platform]} fee component`,
    initial: EMPTY_COMPONENT,
    seed: EMPTY_COMPONENT,
    seedKey: "const",
  });

  const setDraftField = <K extends keyof NewComponentForm>(field: K, next: NewComponentForm[K]) =>
    setDraft((prev) => ({ ...prev, [field]: next }));

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["settings", "platform-fee-components", platform] });
    // Every product's displayed margin is derived from these, so a stale product list would
    // show the old numbers until something else happened to refetch it.
    queryClient.invalidateQueries({ queryKey: ["products"] });
  };

  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<PlatformFeeComponent> }) =>
      feeConfigApi.updateFeeComponent(platform, id, input),
    onSuccess: invalidate,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => feeConfigApi.deleteFeeComponent(platform, id),
    onSuccess: invalidate,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      feeConfigApi.createFeeComponent(platform, {
        name: draft.name,
        basis: draft.basis,
        rate_percent: draft.rate || null,
        fixed_amount: draft.fixed || null,
        display_order: (components?.length ?? 0) + 1,
      }),
    onSuccess: () => {
      invalidate();
      // Resets the fields and the baseline in one go, so the emptied form doesn't read as dirty.
      clearDraft(EMPTY_COMPONENT);
    },
  });
  const createStatus = useSaveStatus(createMutation.status);

  return (
    <div className="flex flex-col gap-2 border-t border-slate-200 pt-3">
      <h3 className="text-sm font-medium">{PLATFORM_LABELS[platform]} fee components</h3>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse bg-white text-left text-xs shadow-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="p-1.5">Name</th>
              <th className="p-1.5">Applies to</th>
              <th className="p-1.5">Rate %</th>
              <th className="p-1.5">Fixed £</th>
              <th className="p-1.5">Enabled</th>
              <th className="p-1.5" />
            </tr>
          </thead>
          <tbody>
            {components?.map((c) => (
              <tr key={c.id} className="border-b border-slate-100">
                <td className="p-1.5">{c.name}</td>
                <td className="p-1.5">{BASIS_LABELS[c.basis]}</td>
                <td className="p-1.5">{c.rate_percent ?? "—"}</td>
                <td className="p-1.5">{c.fixed_amount ?? "—"}</td>
                <td className="p-1.5">
                  <input
                    type="checkbox"
                    aria-label={`${c.name} enabled`}
                    checked={c.enabled}
                    onChange={(e) => updateMutation.mutate({ id: c.id, input: { enabled: e.target.checked } })}
                  />
                </td>
                <td className="p-1.5">
                  <button onClick={() => deleteMutation.mutate(c.id)} className="text-red-600">
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ErrorBanner error={updateMutation.error ?? deleteMutation.error ?? createMutation.error} />
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          createMutation.mutate();
        }}
      >
        <input
          required
          placeholder="Component name"
          aria-label="Component name"
          className="rounded border border-slate-300 px-2 py-1 text-sm"
          value={draft.name}
          onChange={(e) => setDraftField("name", e.target.value)}
        />
        <select
          aria-label="Applies to"
          className="rounded border border-slate-300 px-2 py-1 text-sm"
          value={draft.basis}
          onChange={(e) => setDraftField("basis", e.target.value as FeeBasis)}
        >
          {(Object.keys(BASIS_LABELS) as FeeBasis[]).map((basis) => (
            <option key={basis} value={basis}>
              {BASIS_LABELS[basis]}
            </option>
          ))}
        </select>
        <input
          placeholder="Rate %"
          aria-label="Rate %"
          className="w-20 rounded border border-slate-300 px-2 py-1 text-sm"
          value={draft.rate}
          onChange={(e) => setDraftField("rate", e.target.value)}
        />
        <input
          placeholder="Fixed £"
          aria-label="Fixed £"
          className="w-20 rounded border border-slate-300 px-2 py-1 text-sm"
          value={draft.fixed}
          onChange={(e) => setDraftField("fixed", e.target.value)}
        />
        <SaveButton
          type="submit"
          isDirty={draftDirty}
          isPending={createMutation.isPending}
          status={createStatus}
          // A name is the only required field, so gate on that rather than on dirtiness —
          // the form is an action, not a save. It still reports dirty to the registry.
          enabledWhen={!!draft.name.trim()}
          className="rounded border border-slate-300 px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50"
        >
          + Add component
        </SaveButton>
      </form>
      <p className="text-xs text-slate-400">
        Rates seeded from research in July 2026 — platforms change these periodically, so re-check against{" "}
        {PLATFORM_LABELS[platform]}'s own fee pages if margins look off.
      </p>
    </div>
  );
}
