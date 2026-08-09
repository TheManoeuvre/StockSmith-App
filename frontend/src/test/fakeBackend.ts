/**
 * A tiny path-routed stand-in for the backend, intended to be installed at `api/client` so
 * every api module (products, materials, variants, platforms, …) runs against it unchanged.
 * One mock instead of one per module — and it means route-level tests exercise the real api
 * layer rather than a hand-stubbed shape that can drift from it.
 *
 * `vi.mock` resolves its path relative to the file that calls it and is hoisted above
 * imports, so the test file owns the mock call and pulls the implementation from here:
 *
 *     vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
 */

export interface FakeRoute {
  method: "GET" | "PUT" | "POST" | "PATCH" | "DELETE";
  path: string | RegExp;
  respond: (body: unknown) => unknown;
}

export const calls: { method: string; path: string; body?: unknown }[] = [];

let routes: FakeRoute[] = [];

export function setRoutes(next: FakeRoute[]): void {
  routes = next;
  calls.length = 0;
}

function handle(method: FakeRoute["method"], path: string, body?: unknown): Promise<unknown> {
  calls.push({ method, path, body });
  const route = routes.find(
    (r) => r.method === method && (typeof r.path === "string" ? r.path === path : r.path.test(path))
  );
  // Resolving to undefined instead would surface much later as an unrelated render failure,
  // so an unrouted call fails loudly and names itself.
  if (!route) return Promise.reject(new Error(`fakeBackend: no route for ${method} ${path}`));
  return Promise.resolve(route.respond(body));
}

const notImplemented = () => Promise.reject(new Error("not available in tests"));

export function clientMock() {
  return {
    api: {
      get: (path: string) => handle("GET", path),
      put: (path: string, body?: unknown) => handle("PUT", path, body),
      post: (path: string, body?: unknown) => handle("POST", path, body),
      patch: (path: string, body?: unknown) => handle("PATCH", path, body),
      delete: (path: string) => handle("DELETE", path),
    },
    ApiError: class ApiError extends Error {},
    platformFetch: notImplemented,
    healthCheck: () => Promise.resolve(true),
    fetchSystemStatus: () => handle("GET", "/system/status"),
    assetDownloadUrl: notImplemented,
    assetThumbnailUrl: notImplemented,
    assetUploadUrl: notImplemented,
    materialImageDownloadUrl: notImplemented,
    materialImageThumbnailUrl: notImplemented,
    materialImageUploadUrl: notImplemented,
    shopIconUrl: notImplemented,
    backupDownloadUrl: notImplemented,
    uploadCsv: notImplemented,
    downloadCsv: notImplemented,
  };
}
