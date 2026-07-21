import { getSettings } from "../lib/tauri";

const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

// The Tauri http plugin's fetch bypasses the webview's CORS/CSP restrictions when talking
// to a remote host; plain browser `fetch` is used instead outside a real Tauri build (e.g.
// `vite dev` for fast iteration), where that bridge isn't present.
export async function platformFetch(url: string, init?: RequestInit): Promise<Response> {
  if (!isTauri) return fetch(url, init);
  const { fetch: tauriFetch } = await import("@tauri-apps/plugin-http");
  return tauriFetch(url, init);
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
  }
}

async function authHeaders(): Promise<Record<string, string>> {
  const { sharedPassword } = await getSettings();
  return sharedPassword ? { Authorization: `Bearer ${sharedPassword}` } : {};
}

async function baseUrl(): Promise<string> {
  const { backendUrl } = await getSettings();
  if (!backendUrl) throw new Error("Backend URL is not configured. Set it in Settings.");
  return backendUrl.replace(/\/$/, "");
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const url = `${await baseUrl()}/api/v1${path}`;
  const headers = { ...(await authHeaders()), ...(init.headers as Record<string, string>) };
  const response = await platformFetch(url, { ...init, headers });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    const detail = (() => {
      try {
        return JSON.parse(body)?.detail;
      } catch {
        return undefined;
      }
    })();
    throw new ApiError(response.status, detail || body || response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export async function healthCheck(backendUrl: string): Promise<boolean> {
  try {
    const response = await platformFetch(`${backendUrl.replace(/\/$/, "")}/healthz`);
    return response.ok;
  } catch {
    return false;
  }
}

export async function assetDownloadUrl(assetId: number): Promise<{ url: string; headers: Record<string, string> }> {
  return { url: `${await baseUrl()}/api/v1/assets/${assetId}/download`, headers: await authHeaders() };
}

export async function assetThumbnailUrl(assetId: number): Promise<{ url: string; headers: Record<string, string> }> {
  return { url: `${await baseUrl()}/api/v1/assets/${assetId}/thumbnail`, headers: await authHeaders() };
}

export async function assetUploadUrl(productId: number): Promise<string> {
  return `${await baseUrl()}/api/v1/products/${productId}/assets`;
}

export async function materialImageDownloadUrl(materialId: number): Promise<{ url: string; headers: Record<string, string> }> {
  return {
    url: `${await baseUrl()}/api/v1/materials/${materialId}/image/download`,
    headers: await authHeaders(),
  };
}

export async function materialImageThumbnailUrl(materialId: number): Promise<{ url: string; headers: Record<string, string> }> {
  return {
    url: `${await baseUrl()}/api/v1/materials/${materialId}/image/thumbnail`,
    headers: await authHeaders(),
  };
}

export async function materialImageUploadUrl(materialId: number): Promise<string> {
  return `${await baseUrl()}/api/v1/materials/${materialId}/image`;
}

export interface CsvImportResult {
  created: number;
  updated: number;
  failed: { row: number; error: string }[];
}

export async function uploadCsv(path: string, fileBytes: Uint8Array, filename: string): Promise<CsvImportResult> {
  const url = `${await baseUrl()}/api/v1${path}`;
  const headers = await authHeaders();
  const formData = new FormData();
  formData.append("file", new Blob([fileBytes], { type: "text/csv" }), filename);
  const response = await platformFetch(url, { method: "POST", headers, body: formData });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new ApiError(response.status, body || response.statusText);
  }
  return (await response.json()) as CsvImportResult;
}

export async function downloadCsv(path: string, filename: string): Promise<void> {
  const url = `${await baseUrl()}/api/v1${path}`;
  const response = await platformFetch(url, { headers: await authHeaders() });
  if (!response.ok) throw new ApiError(response.status, await response.text().catch(() => response.statusText));
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}
