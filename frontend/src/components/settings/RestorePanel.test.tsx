import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());

const tauriState = { isHost: true, hostname: "127.0.0.1" };
const restartSpy = vi.fn().mockResolvedValue(undefined);
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  isHostDevice: () => Promise.resolve(tauriState.isHost),
  backendHostname: () => Promise.resolve(tauriState.hostname),
  restartApp: () => restartSpy(),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { RestorePanel } = await import("./RestorePanel");

const BACKUP = {
  filename: "stocksmith-backup-20260809-030000.zip",
  location: "primary",
  size_bytes: 2_500_000,
  manifest: {
    format_version: 1,
    app_version: "0.6.0",
    alembic_revision: "c4e8f21a7b93",
    created_at: "2026-08-09T03:00:00Z",
    kind: "scheduled",
    db_bytes: 120_000,
    asset_file_count: 42,
    asset_bytes: 2_300_000,
    counts: { products: 120, materials: 88 },
    includes_config: false,
    skipped_assets: [],
  },
};

function routes(pending: Record<string, unknown> = { staged: false }) {
  return [
    { method: "GET" as const, path: "/backups", respond: () => [BACKUP] },
    { method: "GET" as const, path: "/restore/pending", respond: () => pending },
    { method: "POST" as const, path: "/restore/stage", respond: () => BACKUP.manifest },
    { method: "DELETE" as const, path: "/restore/pending", respond: () => null },
  ];
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RestorePanel />
    </QueryClientProvider>
  );
}

describe("RestorePanel", () => {
  beforeEach(() => {
    setRoutes(routes());
    tauriState.isHost = true;
    tauriState.hostname = "127.0.0.1";
    restartSpy.mockClear();
  });

  describe("on a thin client", () => {
    beforeEach(() => {
      tauriState.isHost = false;
      tauriState.hostname = "homebase.tailnet.ts.net";
    });

    it("explains where to go instead of offering a button that would fail", async () => {
      renderPanel();

      // Restore needs the backend to stop and come back, and only the shell on the host can
      // restart its own sidecar. The server enforces this too — this is so it doesn't look
      // arbitrary from the other device.
      expect(await screen.findByText(/runs on the computer that hosts StockSmith/)).toBeInTheDocument();
      expect(screen.getByText(/homebase.tailnet.ts.net/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Restore this/ })).not.toBeInTheDocument();
    });

    it("does not poll for a staged restore it could not act on", async () => {
      renderPanel();
      await screen.findByText(/runs on the computer that hosts StockSmith/);

      expect(calls.some((c) => c.path === "/restore/pending")).toBe(false);
    });
  });

  describe("choosing a backup", () => {
    it("demands a typed confirmation", async () => {
      const user = userEvent.setup();
      renderPanel();

      await user.click(await screen.findByRole("button", { name: "Restore this" }));

      const dialog = within(await screen.findByRole("dialog"));
      const confirm = dialog.getByRole("button", { name: "Restore and restart" });
      expect(confirm).toBeDisabled();

      await user.type(dialog.getByRole("textbox"), "RESTORE");
      expect(confirm).toBeEnabled();
    });

    it("states what will be lost and what has to be reconnected", async () => {
      const user = userEvent.setup();
      renderPanel();

      await user.click(await screen.findByRole("button", { name: "Restore this" }));
      const dialog = within(await screen.findByRole("dialog"));

      expect(dialog.getByText(/permanently replaced/)).toBeInTheDocument();
      // Driven off manifest.includes_config, not a hard-coded string.
      expect(dialog.getByText(/reconnect Etsy and eBay/)).toBeInTheDocument();
      expect(dialog.getByText(/snapshot of your current data/)).toBeInTheDocument();
    });

    it("stages the restore once confirmed", async () => {
      const user = userEvent.setup();
      renderPanel();

      await user.click(await screen.findByRole("button", { name: "Restore this" }));
      const dialog = within(await screen.findByRole("dialog"));
      await user.type(dialog.getByRole("textbox"), "RESTORE");
      await user.click(dialog.getByRole("button", { name: "Restore and restart" }));

      await waitFor(() => {
        const staged = calls.find((c) => c.method === "POST" && c.path === "/restore/stage");
        expect(staged?.body).toEqual({ filename: BACKUP.filename });
      });
    });

    it("does nothing when cancelled", async () => {
      const user = userEvent.setup();
      renderPanel();

      await user.click(await screen.findByRole("button", { name: "Restore this" }));
      await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));

      expect(calls.some((c) => c.path === "/restore/stage")).toBe(false);
    });
  });

  describe("with a restore already staged", () => {
    beforeEach(() =>
      setRoutes(
        routes({
          staged: true,
          source_filename: BACKUP.filename,
          requested_at: "2026-08-09T12:00:00Z",
          manifest: BACKUP.manifest,
        })
      )
    );

    it("says the app is waiting to restart", async () => {
      renderPanel();

      // Without this the backend sits in maintenance with nothing on screen explaining why
      // everything else stopped working.
      expect(await screen.findByText(/waiting to be applied/)).toBeInTheDocument();
    });

    it("can restart to apply it", async () => {
      const user = userEvent.setup();
      renderPanel();

      await user.click(await screen.findByRole("button", { name: "Restart now" }));

      await waitFor(() => expect(restartSpy).toHaveBeenCalledOnce());
    });

    it("can cancel it, which is the only way out of maintenance mode", async () => {
      const user = userEvent.setup();
      renderPanel();

      await user.click(await screen.findByRole("button", { name: "Cancel restore" }));

      await waitFor(() =>
        expect(calls.some((c) => c.method === "DELETE" && c.path === "/restore/pending")).toBe(true)
      );
    });
  });
});
