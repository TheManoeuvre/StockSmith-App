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
