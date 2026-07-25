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
    // A small bounded retry (2 attempts, default exponential backoff) rather than none —
    // this is specifically defense in depth against transient backend hiccups like the
    // one that used to make the Dashboard render blank on boot: a DB connection-pool
    // timeout or a sidecar still finishing startup, either of which resolves itself
    // within a couple of seconds without any user action. `retry: false` turned exactly
    // that kind of transient failure into a permanent error screen until the user
    // manually navigated away and back. Most *other* failures here (bad backend URL,
    // wrong password) won't resolve by retrying either, but 2 quick attempts is a small
    // enough cost not to matter for those.
    //
    // One real caveat, confirmed against query-core's retryer (retryer.ts's canContinue):
    // it checks focusManager.isFocused() before every retry attempt regardless of
    // networkMode — if the app window is minimized/backgrounded during the brief backoff
    // window, the retry pauses (not indefinitely — it resumes automatically once the
    // window regains focus) rather than completing while hidden. Acceptable for 2 quick
    // attempts fired at page-load time while the user is actively looking at the app; not
    // worth reintroducing retry:false over.
    //
    // Mutations deliberately don't opt into this — retrying a write is a different risk
    // profile than retrying a read, so they keep the default of no retry.
    //
    // staleTime keeps recently-fetched data cached across quick re-mounts — e.g. switching
    // tabs on the product detail page, where several sections independently query the same
    // (expensive) variants list. Mutations still force a refetch via invalidateQueries
    // regardless of this window, so edits are never masked by stale cache.
    queries: { networkMode: "always", retry: 2, staleTime: 30_000 },
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
