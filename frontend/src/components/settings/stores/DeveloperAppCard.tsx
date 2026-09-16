import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { platformsApi, type PlatformEnvironment } from "../../../api/platforms";
import type { ListingPlatform } from "../../../api/types";
import { PLATFORM_LABELS } from "../../../lib/platforms";
import { useGuard } from "../../../hooks/useUnsavedChangesGuard";
import { Disclosure } from "../Disclosure";
import { EbaySigningKeyPanel } from "../EbaySigningKeyPanel";
import { PlatformCredentialsForm } from "../PlatformCredentialsForm";
import { SettingsCard } from "../SettingsCard";

/**
 * The set-up-once section: the developer app registered with the marketplace, and for eBay
 * the environment it targets and the signing key its fee reporting needs.
 *
 * The environment toggle lives here, in the card's header, rather than inside the
 * credentials strip where it used to be — it decides what "Connect" at the top of the store
 * page targets, and a control that changes a button two panels up shouldn't be tucked
 * inside a collapsed form.
 */
export function DeveloperAppCard({
  platform,
  environment,
  onEnvironmentChange,
}: {
  platform: ListingPlatform;
  environment: PlatformEnvironment;
  onEnvironmentChange: (environment: PlatformEnvironment) => void;
}) {
  const label = PLATFORM_LABELS[platform];
  const guard = useGuard();
  const [credentialsOpen, setCredentialsOpen] = useState(false);

  const { data: credentials } = useQuery({
    queryKey: ["platforms", platform, "credentials", environment],
    queryFn: () => platformsApi.getCredentials(platform, environment),
  });
  const { data: signingKey } = useQuery({
    queryKey: ["platforms", "ebay", "signing-key", environment],
    queryFn: () => platformsApi.getEbaySigningKey(environment),
    enabled: platform === "ebay",
  });

  const credentialsSummary = credentials?.client_id
    ? `${credentials.client_id} · secret ${credentials.client_secret_set ? "configured" : "not set"}`
    : "not set";

  return (
    <SettingsCard
      title="Developer app"
      help={`The app registered in ${label}'s developer portal. Set once; only changes if you re-register it.`}
      action={
        platform === "ebay" ? (
          <div
            role="group"
            aria-label="Environment"
            className="flex gap-0.5 rounded-lg bg-slate-100 p-0.5 text-xs"
          >
            {(["production", "sandbox"] as const).map((env) => (
              <button
                key={env}
                type="button"
                aria-pressed={environment === env}
                // Switching environment re-seeds the credentials form, discarding edits just as
                // surely as collapsing it would — so it goes through the same veto.
                onClick={() =>
                  guard.attempt(() => onEnvironmentChange(env), {
                    prefix: "credentials",
                  })
                }
                className={`rounded-md px-2.5 py-1 font-medium ${
                  environment === env
                    ? env === "sandbox"
                      ? "bg-amber-100 text-amber-800 shadow-sm"
                      : "bg-white text-slate-900 shadow-sm"
                    : "text-slate-500 hover:text-slate-700"
                }`}
              >
                {env === "sandbox" ? "Sandbox" : "Production"}
              </button>
            ))}
          </div>
        ) : undefined
      }
    >
      <div className="-mx-4 -mb-4 border-t border-slate-100">
        <Disclosure
          title="Credentials"
          summary={<span className={credentials?.client_id ? "font-mono" : ""}>{credentialsSummary}</span>}
          tone={credentials?.client_id ? "normal" : "warning"}
          open={credentialsOpen}
          // Collapsing unmounts the form, and a React unmount can't be cancelled after the fact —
          // so the veto has to happen before the state setter runs. Same pattern as VariantEditor.
          onOpenChange={(next) =>
            guard.attempt(() => setCredentialsOpen(next), {
              prefix: "credentials",
            })
          }
        >
          <PlatformCredentialsForm platform={platform} environment={environment} />
        </Disclosure>
        {platform === "ebay" && (
          <Disclosure
            title="Fee reporting signature"
            summary={
              signingKey?.configured
                ? `configured${signingKey.expires_at ? ` · expires ${new Date(signingKey.expires_at).toLocaleDateString()}` : ""}`
                : "not set up — eBay won't report fees on your orders until it is"
            }
            tone={signingKey?.configured ? "normal" : "warning"}
          >
            <EbaySigningKeyPanel environment={environment} />
          </Disclosure>
        )}
      </div>
    </SettingsCard>
  );
}
