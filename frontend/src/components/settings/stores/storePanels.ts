/**
 * Sections of a store's settings page that a link can scroll to via `?panel=`. Kept apart from
 * StorePage so the components that carry the DOM ids (ListingProfiles, PlatformLimitsEditor) can
 * import them without importing the page that imports them.
 */
export const STORE_PANEL_IDS = ["listing-profiles", "profile-suggestions", "listing-limits"] as const;
export type StorePanelId = (typeof STORE_PANEL_IDS)[number];

export const STORE_PANEL_DOM_IDS: Record<StorePanelId, string> = {
  "listing-profiles": "store-panel-listing-profiles",
  "profile-suggestions": "store-panel-profile-suggestions",
  "listing-limits": "store-panel-listing-limits",
};
