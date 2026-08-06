import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { platformsApi, type PlatformEnvironment } from "../../api/platforms";
import { ErrorBanner } from "../common/ErrorBanner";

// eBay requires a digital signature (RFC 9421) on its in-scope APIs when they're called
// for an EU/UK-domiciled seller. StockSmith reads marketplace fees from one of those —
// the Sell Finances API — so without a keypair every fee lookup comes back
//
//   403  errorId 215001  "Missing x-ebay-signature-key header to fulfill the request."
//
// and eBay orders import with no fee figure, which quietly overstates net profit on every
// one of them. This panel is the only place that gap is visible: order sync itself keeps
// working perfectly, so nothing else in the app would ever mention it.
export function EbaySigningKeyPanel({ environment }: { environment: PlatformEnvironment }) {
  const queryClient = useQueryClient();

  const { data } = useQuery({
    queryKey: ["platforms", "ebay", "signing-key", environment],
    queryFn: () => platformsApi.getEbaySigningKey(environment),
  });

  const createMutation = useMutation({
    mutationFn: () => platformsApi.createEbaySigningKey(environment),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["platforms", "ebay", "signing-key", environment] });
    },
  });

  const configured = data?.configured ?? false;

  return (
    <div className="flex flex-col gap-2 border-t border-slate-200 pt-2">
      <div className="flex items-center justify-between gap-3">
        <div className="flex flex-col">
          <span className="text-sm font-medium">Fee reporting signature ({environment})</span>
          <span className="text-xs text-slate-500">
            {configured ? (
              <>
                Configured
                {data?.signing_key_id ? <span className="font-mono"> · {data.signing_key_id}</span> : null}
                {data?.expires_at ? ` · expires ${new Date(data.expires_at).toLocaleDateString()}` : ""}
              </>
            ) : (
              "Not set up — eBay will not report platform fees for your orders until it is."
            )}
          </span>
        </div>
        <button
          onClick={() => createMutation.mutate()}
          disabled={createMutation.isPending}
          className="shrink-0 rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
        >
          {createMutation.isPending ? "Creating…" : configured ? "Replace key" : "Set up"}
        </button>
      </div>
      {configured && (
        // eBay hands the private key over exactly once, at creation, and keeps no copy —
        // replacing it is not reversible and the old key cannot be restored from
        // anywhere. Worth saying plainly next to a button that does it in one click.
        <p className="text-xs text-slate-500">
          Replacing mints a new keypair. eBay issues the private half once and stores no copy, so the current key
          cannot be recovered afterwards.
        </p>
      )}
      <ErrorBanner error={createMutation.error} />
    </div>
  );
}
