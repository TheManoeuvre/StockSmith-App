// Isolates all Tauri-plugin-specific calls so the rest of the app can run/iterate
// in a plain browser (e.g. `vite dev`) against the backend without a full Tauri build.
// Falls back to localStorage/no-ops outside a real Tauri webview, since the plugin IPC
// bridge (window.__TAURI_INTERNALS__) doesn't exist there and would otherwise hang forever.

const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export interface AppSettings {
  backendUrl: string;
  sharedPassword: string;
}

const LOCAL_STORAGE_KEY = "stocksmith-settings";

export async function getSettings(): Promise<Partial<AppSettings>> {
  if (!isTauri) {
    const raw = localStorage.getItem(LOCAL_STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  }
  const { LazyStore } = await import("@tauri-apps/plugin-store");
  const store = new LazyStore("settings.json");
  const backendUrl = (await store.get<string>("backendUrl")) ?? undefined;
  const sharedPassword = (await store.get<string>("sharedPassword")) ?? undefined;
  return { backendUrl, sharedPassword };
}

export async function saveSettings(settings: AppSettings): Promise<void> {
  if (!isTauri) {
    localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(settings));
    return;
  }
  const { LazyStore } = await import("@tauri-apps/plugin-store");
  const store = new LazyStore("settings.json");
  await store.set("backendUrl", settings.backendUrl);
  await store.set("sharedPassword", settings.sharedPassword);
  await store.save();
}

// Packaged desktop builds auto-provision the connection on first launch, so the user
// never has to manually type a URL/password: the bundled backend writes a one-time-use
// bootstrap-info endpoint (see app/main.py's bootstrap_info), which this reads and saves
// before the app renders.
//
// main.tsx uses this same call to gate rendering the router (and its first data-fetching
// component, the Dashboard) behind backend readiness — showing a splash screen for the
// gap instead of racing a backend that isn't listening yet. That means this must always
// poll until the backend actually answers, on *every* launch, not just the first: once
// settings are saved, provisioning itself has nothing left to do, but the sidecar backend
// (spawned in parallel by Rust's own setup hook, see lib.rs's wait_for_backend_ready) still
// takes a few seconds to come up on every cold start. Returning early here used to skip
// that wait entirely on the 2nd+ launch — ready flipped true almost instantly, the Dashboard's
// first fetch raced a backend/connection-pool that wasn't ready yet, and it rendered blank
// until the user navigated away and back (by which point the backend had caught up).
//
// Retries rather than trying once, since the wait above can take a few seconds. Any
// response at all — including the 404 an already-consumed endpoint returns — proves the
// backend is live and answering. Polls on the same cadence/timeout as Rust's own
// wait_for_backend_ready (lib.rs) so both give up around the same time rather than this
// failing silently well before Rust would.
const AUTO_PROVISION_TIMEOUT_MS = 20_000;
const AUTO_PROVISION_POLL_INTERVAL_MS = 500;

export async function tryAutoProvisionSettings(): Promise<void> {
  if (!isTauri) return;
  const existing = await getSettings();
  // Only *save* newly-provisioned settings if we don't already have a configured
  // connection (don't clobber a manually-set advanced connection) — but keep polling
  // below regardless, since that's also this function's backend-readiness gate.
  const needsProvisioning = !(existing.backendUrl && existing.sharedPassword);

  const { fetch: tauriFetch } = await import("@tauri-apps/plugin-http");
  const deadline = Date.now() + AUTO_PROVISION_TIMEOUT_MS;

  while (Date.now() < deadline) {
    try {
      const response = await tauriFetch("http://127.0.0.1:8000/bootstrap-info");
      if (response.ok) {
        if (needsProvisioning) {
          const { backendUrl, sharedPassword } = (await response.json()) as AppSettings;
          if (backendUrl && sharedPassword) {
            await saveSettings({ backendUrl, sharedPassword });
          }
        }
        return;
      }
      if (response.status === 404) {
        // Backend is up and answering — the endpoint is just already consumed (every
        // launch after the first) or this is a dev backend with no bootstrap.py at all.
        // Either way, the readiness question this loop exists to answer is settled.
        return;
      }
    } catch {
      // Nothing answering yet — keep polling until the timeout.
    }
    await new Promise((resolve) => setTimeout(resolve, AUTO_PROVISION_POLL_INTERVAL_MS));
  }
}

export async function openExternalUrl(url: string): Promise<void> {
  if (!isTauri) {
    window.open(url, "_blank", "noopener,noreferrer");
    return;
  }
  const { openUrl } = await import("@tauri-apps/plugin-opener");
  await openUrl(url);
}

export async function pickFile(): Promise<{ path: string; name: string } | null> {
  if (!isTauri) {
    throw new Error("File picking requires the Tauri desktop app (not available in browser preview).");
  }
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({ multiple: false, directory: false });
  if (!selected || Array.isArray(selected)) return null;
  const name = selected.split(/[\\/]/).pop() ?? selected;
  return { path: selected, name };
}

/**
 * Whether this device is the one running the backend.
 *
 * Restore is host-only — applying one needs the backend to stop and come back, and only the
 * shell on the host can restart its own sidecar. The stored backend URL already encodes the
 * distinction: an auto-provisioned host points at loopback, a thin client at a Tailscale name.
 *
 * Used to explain the situation rather than to enforce it. The server enforces (require_host);
 * this just avoids offering a button that would 403.
 */
export async function isHostDevice(): Promise<boolean> {
  const { backendUrl } = await getSettings();
  if (!backendUrl) return false;
  try {
    return ["127.0.0.1", "localhost", "::1", "[::1]"].includes(new URL(backendUrl).hostname);
  } catch {
    return false;
  }
}

/** The host name a thin client is pointed at, for telling the user where to go instead. */
export async function backendHostname(): Promise<string | null> {
  const { backendUrl } = await getSettings();
  if (!backendUrl) return null;
  try {
    return new URL(backendUrl).hostname;
  } catch {
    return null;
  }
}

export async function restartApp(): Promise<void> {
  if (!isTauri) {
    throw new Error("Restarting requires the StockSmith desktop app.");
  }
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("restart_app");
}

/** Whether this is the packaged desktop app at all — the autostart controls below are
 * meaningless in a browser preview, and a toggle that silently does nothing is worse than
 * one that isn't there. */
export function isDesktopApp(): boolean {
  return isTauri;
}

/**
 * Whether Windows is set to start StockSmith at sign-in.
 *
 * Always read from the registry rather than remembered anywhere, because it can change
 * without the app being told: Windows' own Startup Apps settings can turn it off, and there
 * is a standing report of the plugin's entry vanishing after the first boot
 * (plugins-workspace#771, see docs/plan-background-sync.md §3). A cached answer would keep
 * insisting autostart was on in exactly the cases worth catching.
 */
export async function getAutostartEnabled(): Promise<boolean> {
  if (!isTauri) return false;
  const { invoke } = await import("@tauri-apps/api/core");
  return await invoke<boolean>("autostart_enabled");
}

/** Returns the state read back from Windows afterwards, which is not always the state
 * asked for — see getAutostartEnabled. */
export async function setAutostartEnabled(enabled: boolean): Promise<boolean> {
  if (!isTauri) {
    throw new Error("Starting with Windows requires the StockSmith desktop app.");
  }
  const { invoke } = await import("@tauri-apps/api/core");
  return await invoke<boolean>("set_autostart", { enabled });
}

export async function pickDirectory(): Promise<string | null> {
  if (!isTauri) {
    throw new Error("Folder picking requires the Tauri desktop app (not available in browser preview).");
  }
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({ multiple: false, directory: true });
  if (!selected || Array.isArray(selected)) return null;
  return selected;
}

/**
 * Save a URL's response straight to a file the user picks.
 *
 * Deliberately not the Blob + `<a download>` trick that downloadCsv uses (api/client.ts). That
 * buffers the entire response in the webview's memory before writing a byte, which is fine for
 * a few hundred KB of CSV and not fine for a backup archive carrying every product image. The
 * upload plugin's `download` streams to disk and reports progress while it does.
 *
 * Returns the chosen path, or null if the save dialog was dismissed.
 */
export async function saveFileTo(
  url: string,
  headers: Record<string, string>,
  suggestedName: string,
  onProgress?: (loaded: number, total: number) => void
): Promise<string | null> {
  if (!isTauri) {
    throw new Error("Saving a file requires the Tauri desktop app (not available in browser preview).");
  }
  const { save } = await import("@tauri-apps/plugin-dialog");
  const target = await save({ defaultPath: suggestedName });
  if (!target) return null;

  const { download } = await import("@tauri-apps/plugin-upload");
  await download(
    url,
    target,
    (progress) => onProgress?.(progress.progress, progress.total),
    new Map(Object.entries(headers))
  );
  return target;
}

export async function readFileBytes(path: string): Promise<Uint8Array> {
  const { readFile } = await import("@tauri-apps/plugin-fs");
  return readFile(path);
}

export async function uploadFile(
  url: string,
  filePath: string,
  headers: Record<string, string>,
  onProgress?: (loaded: number, total: number) => void
): Promise<void> {
  const { upload } = await import("@tauri-apps/plugin-upload");
  await upload(url, filePath, (progress) => onProgress?.(progress.progress, progress.total), new Map(Object.entries(headers)));
}
