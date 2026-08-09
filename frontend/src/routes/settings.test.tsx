import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", async () => (await import("../test/fakeBackend")).clientMock());

// The settings route reads and writes the Tauri store directly rather than through the api
// layer, so fakeBackend doesn't cover it. Connected by default — that's the state the packaged
// app auto-provisions into, and the one the tab ordering is designed around.
const tauriSettings = { backendUrl: "http://127.0.0.1:8000", sharedPassword: "hunter2" };
vi.mock("../lib/tauri", () => ({
  getSettings: () => Promise.resolve(tauriSettings),
  saveSettings: () => Promise.resolve(),
  openExternalUrl: () => Promise.resolve(),
}));

const { setRoutes } = await import("../test/fakeBackend");
const { routeTree } = await import("../routeTree.gen");

function baseRoutes() {
  return [
    { method: "GET" as const, path: "/settings/default-currency", respond: () => ({ default_currency: "GBP" }) },
    {
      method: "GET" as const,
      path: "/settings/forecast-settings",
      respond: () => ({
        forecast_warning_weeks: "6",
        forecast_critical_weeks: "2",
        forecast_lookback_weeks: 8,
        default_lead_time_weeks: "4",
      }),
    },
    { method: "GET" as const, path: "/settings/margin-fee-config", respond: () => ({ fee_source: "manual" }) },
    { method: "GET" as const, path: /^\/settings\/platform-fee-components\//, respond: () => [] },
    { method: "GET" as const, path: "/shipping-profiles", respond: () => [] },
    { method: "GET" as const, path: "/manufacturers", respond: () => [] },
    { method: "GET" as const, path: "/suppliers", respond: () => [] },
    { method: "GET" as const, path: "/material-types", respond: () => [] },
    { method: "GET" as const, path: "/materials", respond: () => [] },
    { method: "PUT" as const, path: /^\/settings\//, respond: (body: unknown) => body },
    // The object-shaped endpoints the integration cards reach for. Listed explicitly because
    // the catch-all below has to be an array — most of what's left is a list, and handing a
    // component `{}` where it expects one fails as "rows.map is not a function" three layers
    // from the cause.
    {
      method: "GET" as const,
      path: /^\/platforms\/\w+\/status$/,
      respond: () => ({ connected: false, has_shop_icon: false, connected_at: null }),
    },
    { method: "GET" as const, path: /^\/platforms\/\w+\/credentials/, respond: () => ({}) },
    { method: "GET" as const, path: /^\/platforms\/ebay\/signing-key/, respond: () => ({}) },
    { method: "GET" as const, path: /.*/, respond: () => [] },
  ];
}

async function renderSettings(initialPath = "/settings") {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [initialPath] }),
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      <RouterProvider router={router as any} />
    </QueryClientProvider>
  );
  // Generous timeout: the first render in a file resolves the whole lazy route tree.
  await screen.findByRole("heading", { name: "Settings" }, { timeout: 5000 });
  return router;
}

const tab = (name: string) => screen.getByRole("button", { name });

describe("settings page", () => {
  beforeEach(() => {
    setRoutes(baseRoutes());
    tauriSettings.backendUrl = "http://127.0.0.1:8000";
    tauriSettings.sharedPassword = "hunter2";
  });

  describe("tab ordering and default", () => {
    it("leads with General", async () => {
      await renderSettings();

      const labels = screen
        .getAllByRole("button")
        .map((b) => b.textContent)
        .filter((t): t is string => !!t);
      expect(labels.indexOf("General")).toBeLessThan(labels.indexOf("Integrations"));
      expect(labels.indexOf("General")).toBeLessThan(labels.indexOf("Connection"));
    });

    it("opens on General when the connection is already provisioned", async () => {
      await renderSettings();
      expect(await screen.findByText("Default currency")).toBeInTheDocument();
    });

    it("opens on Connection when there is something to fill in", async () => {
      // A thin client on first run, before anyone has typed the host's URL.
      tauriSettings.backendUrl = "";
      tauriSettings.sharedPassword = "";
      await renderSettings();

      expect(await screen.findByRole("button", { name: "Test connection" })).toBeInTheDocument();
    });
  });

  describe("tab state in the URL", () => {
    it("honours ?tab= on load", async () => {
      await renderSettings("/settings?tab=pricing");
      expect(await screen.findByText("Margin estimate basis")).toBeInTheDocument();
    });

    it("writes the tab to the URL when clicked", async () => {
      const user = userEvent.setup();
      const router = await renderSettings();

      await user.click(tab("Reference data"));

      // This is what makes the root unsaved-changes blocker cover tab switches: a search-param
      // change is a real navigation, so no guard code is needed on this route at all.
      await waitFor(() => expect(router.state.location.search).toEqual({ tab: "reference" }));
    });

    it("falls back to the default for an unrecognised tab", async () => {
      await renderSettings("/settings?tab=nonsense");
      expect(await screen.findByText("Default currency")).toBeInTheDocument();
    });
  });

  describe("section placement", () => {
    it("puts shipping profiles under Reference data, not Pricing", async () => {
      const user = userEvent.setup();
      await renderSettings();

      await user.click(tab("Reference data"));
      expect(await screen.findByRole("heading", { name: "Shipping profiles" })).toBeInTheDocument();

      await user.click(tab("Pricing"));
      await screen.findByText("Margin estimate basis");
      expect(screen.queryByRole("heading", { name: "Shipping profiles" })).not.toBeInTheDocument();
    });

    it("shows reference data inline rather than linking out", async () => {
      const user = userEvent.setup();
      await renderSettings();

      await user.click(tab("Reference data"));

      // These used to be <Link>s to standalone /manufacturers-style routes.
      expect(await screen.findByRole("heading", { name: "Manufacturers" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Suppliers" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Material types" })).toBeInTheDocument();
    });

    it("keeps the global fee basis in Pricing and the fee components on each integration", async () => {
      const user = userEvent.setup();
      await renderSettings();

      await user.click(tab("Pricing"));
      expect(await screen.findByText("Margin estimate basis")).toBeInTheDocument();
      // Per-platform fee tables moved out — Pricing keeps only the global lens plus a summary.
      expect(screen.queryByRole("heading", { name: "Etsy fee components" })).not.toBeInTheDocument();

      await user.click(tab("Integrations"));
      expect(await screen.findByRole("heading", { name: "Etsy fee components" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "eBay fee components" })).toBeInTheDocument();
    });
  });
});
