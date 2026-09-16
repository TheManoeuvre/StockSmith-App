import type { ListingPlatform } from "../../../api/types";
import { CONNECTABLE_PLATFORMS } from "../../../lib/platforms";
import { BackgroundSyncSettings } from "../BackgroundSyncSettings";
import { FieldMappingTable } from "../FieldMappingTable";
import { StoreConnectionCard } from "./StoreConnectionCard";

/**
 * The "All stores" view: one card per store answering "is it connected and syncing?", then
 * the two things that are genuinely about every store at once. Everything specific to one
 * store is on that store's page.
 */
export function StoresHub({ onOpenStore }: { onOpenStore: (platform: ListingPlatform) => void }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {CONNECTABLE_PLATFORMS.map((platform) => (
          <StoreConnectionCard key={platform} platform={platform} onOpen={() => onOpenStore(platform)} />
        ))}
      </div>
      <BackgroundSyncSettings />
      <FieldMappingTable />
    </div>
  );
}
