import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
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
  pickDirectory: () => Promise.resolve(null),
  saveFileTo: () => Promise.resolve(null),
  isHostDevice: () => Promise.resolve(true),
  backendHostname: () => Promise.resolve("127.0.0.1"),
  restartApp: () => Promise.resolve(),
  // Not the desktop app here, so the General tab's autostart toggle stays out of the way —
  // it is covered directly in BackgroundSyncSettings.test.tsx.
  isDesktopApp: () => false,
  getAutostartEnabled: () => Promise.resolve(false),
  setAutostartEnabled: () => Promise.resolve(false),
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
    { method: "GET" as const, path: "/colours", respond: () => [] },
    { method: "GET" as const, path: "/materials", respond: () => [] },
    {
      method: "GET" as const,
      path: "/backups/settings",
      respond: () => ({
        supported: true,
        unsupported_reason: null,
        scheduled_enabled: true,
        scheduled_hour_local: 3,
        retention_count: 7,
        secondary_dir: null,
        secondary_dir_last_ok_at: null,
        secondary_dir_last_error: null,
        last_run_at: null,
        last_run_status: null,
        last_run_error: null,
      }),
    },
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
    {
      method: "GET" as const,
      path: /^\/platforms\/sync-health/,
      respond: () => ({
        window_days: 7,
        measurable: false,
        reason: "No marketplace has auto-sync turned on.",
        expected_interval_minutes: null,
        gap_threshold_minutes: null,
        gaps: [],
        total_gap_minutes: 0,
        longest_gap_minutes: 0,
      }),
    },
    { method: "GET" as const, path: /^\/platforms\/ebay\/signing-key/, respond: () => ({}) },
    { method: "GET" as const, path: "/restore/pending", respond: () => ({ staged: false }) },
    {
      method: "GET" as const,
      path: "/system/status",
      respond: () => ({
        status: "ok",
        phase: null,
        app_version: "0.6.0",
        alembic_revision: "c4e8f21a7b93",
        data_fingerprint: "abc:",
        last_restore: null,
      }),
    },
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

    it("gives backups their own tab", async () => {
      const user = userEvent.setup();
      await renderSettings();

      await user.click(tab("Backup"));

      expect(await screen.findByRole("heading", { name: "Backups" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Back up now" })).toBeInTheDocument();
    });

    it("shows reference data inline rather than linking out", async () => {
      const user = userEvent.setup();
      await renderSettings();

      await user.click(tab("Reference data"));

      // These used to be <Link>s to standalone /manufacturers-style routes.
      expect(await screen.findByRole("heading", { name: "Manufacturers" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Suppliers" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Material types" })).toBeInTheDocument();
      // Colours joined the others once it stopped being free text on each material.
      expect(screen.getByRole("heading", { name: "Colours" })).toBeInTheDocument();
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

  describe("unsaved changes", () => {
    const warningField = () => screen.getByLabelText(/Warning threshold/);

    /** The General tab has several cards, each with its own Save — scope to the one under test. */
    const forecastCard = () => {
      const heading = screen.getByRole("heading", { name: "Materials forecasting" });
      const card = heading.closest("div.rounded");
      if (!card) throw new Error("Could not find the forecasting card");
      return within(card as HTMLElement);
    };
    const saveButton = () => forecastCard().getByRole("button", { name: "Save" });
    const dialog = () => within(screen.getByRole("dialog"));

    it("enables Save only once something has actually changed", async () => {
      const user = userEvent.setup();
      await renderSettings();

      await screen.findByRole("heading", { name: "Materials forecasting" });
      expect(saveButton()).toBeDisabled();

      await user.clear(warningField());
      await user.type(warningField(), "9");

      expect(saveButton()).toBeEnabled();
    });

    it("warns before a tab switch discards a dirty settings form", async () => {
      const user = userEvent.setup();
      await renderSettings();

      await screen.findByRole("heading", { name: "Materials forecasting" });
      await user.clear(warningField());
      await user.type(warningField(), "9");

      await user.click(tab("Pricing"));

      // The whole point of Phase 2: the blocker was already mounted at the root and already
      // covered this route — nothing here registered as dirty, so it never fired.
      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      // Names what's unsaved rather than just saying something is.
      expect(dialog().getByText(/Materials forecasting/)).toBeInTheDocument();
    });

    it("stays put when you choose to keep editing", async () => {
      const user = userEvent.setup();
      const router = await renderSettings();

      await screen.findByRole("heading", { name: "Materials forecasting" });
      await user.clear(warningField());
      await user.type(warningField(), "9");
      await user.click(tab("Pricing"));

      await user.click(await screen.findByRole("button", { name: "Keep editing" }));

      expect(router.state.location.search).toEqual({});
      expect(warningField()).toHaveValue(9);
    });

    it("lets the tab switch through once saved", async () => {
      const user = userEvent.setup();
      await renderSettings();

      await screen.findByRole("heading", { name: "Materials forecasting" });
      await user.clear(warningField());
      await user.type(warningField(), "9");
      await user.click(saveButton());

      await waitFor(() => expect(saveButton()).toBeDisabled());

      await user.click(tab("Pricing"));

      expect(await screen.findByText("Margin estimate basis")).toBeInTheDocument();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("warns about a half-typed connection URL", async () => {
      const user = userEvent.setup();
      await renderSettings("/settings?tab=connection");

      await user.click(await screen.findByRole("button", { name: /advanced connection settings/ }));
      await user.type(screen.getByLabelText("Backend URL"), "-typo");

      await user.click(tab("General"));

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      expect(dialog().getByText(/Connection settings/)).toBeInTheDocument();
    });
  });
});
