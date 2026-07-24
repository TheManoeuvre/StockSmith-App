import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";
import { tryAutoProvisionSettings } from "./lib/tauri";
import { SplashScreen } from "./components/common/SplashScreen";
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

function App() {
  // Gates the router (and its data-fetching) behind the bundled backend actually answering
  // requests — tryAutoProvisionSettings polls it until it responds at all (even a 404 counts,
  // see that function's comment), so by the time this resolves the dashboard's first fetch
  // is guaranteed to land on a live backend instead of racing it. Shows the splash screen
  // for that gap instead of leaving the window looking frozen. Falls through on rejection
  // too — an unexpected failure here should never be the reason the whole app fails to
  // render; Settings' manual-entry fields are still the fallback.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    tryAutoProvisionSettings().then(
      () => setReady(true),
      () => setReady(true)
    );
  }, []);

  if (!ready) return <SplashScreen />;
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
