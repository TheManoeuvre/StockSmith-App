import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";
import { tryAutoProvisionSettings } from "./lib/tauri";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    // This app only ever talks to a Tailscale-reachable LAN backend, which the browser's
    // online/offline detection knows nothing about — React Query's default `networkMode:
    // 'online'` would otherwise pause queries based on that unrelated signal.
    //
    // Retries are disabled outright: most failures here (bad backend URL, wrong password)
    // are config errors that won't resolve by retrying, and query-core's retry path also
    // waits on window focus before retrying even with networkMode: "always" — so a retry
    // queued while the app window is minimized/backgrounded would hang indefinitely rather
    // than ever surfacing the error. Not worth that risk for a desktop app.
    // staleTime keeps recently-fetched data cached across quick re-mounts — e.g. switching
    // tabs on the product detail page, where several sections independently query the same
    // (expensive) variants list. Mutations still force a refetch via invalidateQueries
    // regardless of this window, so edits are never masked by stale cache.
    queries: { networkMode: "always", retry: false, staleTime: 30_000 },
    mutations: { networkMode: "always" },
  },
});
const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

function renderApp() {
  ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </React.StrictMode>
  );
}

// Populates backendUrl/sharedPassword from the bundled backend's one-time bootstrap-info
// endpoint before any route (or its data-fetching) ever mounts — otherwise the first
// render could fire off requests with no backendUrl configured yet. Falls through to
// renderApp on rejection too — an unexpected failure here should never be the reason the
// whole app fails to render; Settings' manual-entry fields are still the fallback.
tryAutoProvisionSettings().then(renderApp, renderApp);
