// Desktop-updater calls, kept apart from lib/tauri.ts so the many tests that mock that module
// don't each have to know about them. Outside the Tauri webview (vite dev, tests) every call is
// a harmless no-op, as in tauri.ts.

const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** Whether updates can be checked at all: only the packaged desktop app has an updater. */
export function canUpdate(): boolean {
  return isTauri;
}

/** The version of this install — the shell's own, not the backend's, since on a thin client the
 * backend is somebody else's machine. `null` outside the desktop app. */
export async function getInstalledVersion(): Promise<string | null> {
  if (!isTauri) return null;
  const { getVersion } = await import("@tauri-apps/api/app");
  return getVersion();
}

export interface AvailableUpdate {
  version: string;
  /** The release body (CHANGELOG section) of the newest release only. */
  notes: string | null;
  /** ISO date the release was published, when latest.json carries one. */
  date: string | null;
}

// The updater hands back an object that downloads and installs itself; the UI only needs the
// plain facts above, so the live object stays here for installUpdate to find.
let pendingUpdate: import("@tauri-apps/plugin-updater").Update | null = null;

/** Asks the release feed whether a newer signed build exists. Resolves null when up to date,
 * and outside the desktop app, where there is nothing to update. Rejects on a failed check. */
export async function checkForUpdate(): Promise<AvailableUpdate | null> {
  if (!isTauri) return null;
  const { check } = await import("@tauri-apps/plugin-updater");
  const update = await check();
  pendingUpdate = update;
  if (!update) return null;
  return { version: update.version, notes: update.body ?? null, date: update.date ?? null };
}

/**
 * Downloads and installs the update found by the last checkForUpdate, then the installer
 * restarts the app.
 *
 * Download first, stop the backend second, install third. Windows can't replace a running
 * sidecar, so it has to be dead before the installer runs — but stopping it before the download
 * would leave the user with no backend for the length of a slow download, or for good if the
 * download failed. If the install itself fails the backend is already gone, so the app restarts
 * to bring a fresh one up rather than leaving a window with nothing behind it.
 */
export async function installUpdate(onProgress?: (loaded: number, total: number | null) => void): Promise<void> {
  if (!isTauri || !pendingUpdate) {
    throw new Error("Installing an update requires the StockSmith desktop app.");
  }
  const update = pendingUpdate;
  let loaded = 0;
  let total: number | null = null;
  await update.download((event) => {
    if (event.event === "Started") total = event.data.contentLength ?? null;
    if (event.event === "Progress") loaded += event.data.chunkLength;
    onProgress?.(loaded, total);
  });

  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("stop_backend_for_update");
  try {
    await update.install();
  } catch (error) {
    await invoke("restart_app");
    throw error;
  }
}

/** Calls back when the tray's "Check for updates" is clicked. Returns an unsubscribe function;
 * a no-op outside the desktop app. */
export async function onCheckForUpdatesRequested(callback: () => void): Promise<() => void> {
  if (!isTauri) return () => {};
  const { listen } = await import("@tauri-apps/api/event");
  return listen("check-for-updates", callback);
}

const IGNORED_UPDATE_KEY = "ignoredUpdateVersion";
const IGNORED_UPDATE_LOCAL_KEY = "stocksmith-ignored-update";

/** The release the user chose to ignore, if any. Per device, because each device installs its
 * own copy of the app. */
export async function getIgnoredUpdateVersion(): Promise<string | null> {
  if (!isTauri) return localStorage.getItem(IGNORED_UPDATE_LOCAL_KEY);
  const { LazyStore } = await import("@tauri-apps/plugin-store");
  const store = new LazyStore("settings.json");
  return (await store.get<string>(IGNORED_UPDATE_KEY)) ?? null;
}

export async function setIgnoredUpdateVersion(version: string | null): Promise<void> {
  if (!isTauri) {
    if (version) localStorage.setItem(IGNORED_UPDATE_LOCAL_KEY, version);
    else localStorage.removeItem(IGNORED_UPDATE_LOCAL_KEY);
    return;
  }
  const { LazyStore } = await import("@tauri-apps/plugin-store");
  const store = new LazyStore("settings.json");
  if (version) await store.set(IGNORED_UPDATE_KEY, version);
  else await store.delete(IGNORED_UPDATE_KEY);
  await store.save();
}
