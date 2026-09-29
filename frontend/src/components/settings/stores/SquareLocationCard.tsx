import type { PlatformStatus } from "../../../api/platforms";
import { SettingsCard } from "../SettingsCard";

/**
 * Square orders belong to one location, and this app only ever syncs one — chosen at
 * connect time via SquareConnectDialog, stored in account_id (the same field Etsy/eBay use
 * for "which shop/account is this"; see routers/platforms.set_square_location). This card
 * just shows which one and lets it be changed, reusing the same dialog.
 */
export function SquareLocationCard({
  status,
  onChangeLocation,
}: {
  status: PlatformStatus | undefined;
  onChangeLocation: () => void;
}) {
  return (
    <SettingsCard title="Location">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-slate-600">
          {status?.account_id ? (
            <>
              Syncing orders from location <span className="font-mono text-xs">{status.account_id}</span>.
            </>
          ) : (
            "No location selected yet — orders can't sync until one is chosen."
          )}
        </p>
        <button
          type="button"
          onClick={onChangeLocation}
          className="h-7 flex-none rounded-md border border-slate-300 bg-white px-2.5 text-xs font-medium text-slate-900 hover:bg-slate-50"
        >
          {status?.account_id ? "Change" : "Choose location"}
        </button>
      </div>
    </SettingsCard>
  );
}
