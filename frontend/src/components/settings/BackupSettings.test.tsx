import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());
vi.mock("../../lib/tauri", () => ({
  getSettings: () => Promise.resolve({ backendUrl: "http://127.0.0.1:8000", sharedPassword: "x" }),
  pickDirectory: () => Promise.resolve(null),
  saveFileTo: () => Promise.resolve(null),
}));

const { setRoutes, calls } = await import("../../test/fakeBackend");
const { BackupSettings } = await import("./BackupSettings");

const SETTINGS = {
  supported: true,
  unsupported_reason: null,
  scheduled_enabled: true,
  scheduled_hour_local: 3,
  retention_count: 7,
  secondary_dir: null,
  secondary_dir_last_ok_at: null,
  secondary_dir_last_error: null,
  last_run_at: "2026-08-09T02:00:00Z",
  last_run_status: "ok",
  last_run_error: null,
};

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
    counts: { products: 120, materials: 88, orders: 0 },
    includes_config: false,
    skipped_assets: [],
  },
};

function routes(settings: Record<string, unknown> = SETTINGS, backups: unknown[] = [BACKUP]) {
  return [
    { method: "GET" as const, path: "/backups/settings", respond: () => settings },
    { method: "GET" as const, path: "/backups", respond: () => backups },
    { method: "PUT" as const, path: "/backups/settings", respond: (body: unknown) => ({ ...settings, ...(body as object) }) },
    { method: "POST" as const, path: "/backups", respond: () => BACKUP },
    { method: "DELETE" as const, path: /^\/backups\//, respond: () => null },
  ];
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BackupSettings />
    </QueryClientProvider>
  );
}

describe("BackupSettings", () => {
  beforeEach(() => setRoutes(routes()));

  it("shows when the last backup ran", async () => {
    renderPanel();
    expect(await screen.findByText(/Last backup:/)).toBeInTheDocument();
  });

  it("takes a backup on demand", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("button", { name: "Back up now" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/backups")).toBe(true));
  });

  it("lists what a backup contains, not just its filename", async () => {
    renderPanel();

    // The filename is a timestamp; what tells you whether this is the backup you want is the
    // date and what's in it.
    expect(await screen.findByText(/120 products/)).toBeInTheDocument();
    expect(screen.getByText(/88 materials/)).toBeInTheDocument();
    // Zero-count tables are omitted rather than listed as "0 orders".
    expect(screen.queryByText(/0 orders/)).not.toBeInTheDocument();
    expect(screen.getByText("42 asset files")).toBeInTheDocument();
    expect(screen.getByText("2.4 MB")).toBeInTheDocument();
  });

  describe("schedule form", () => {
    it("buffers changes rather than saving per keystroke", async () => {
      const user = userEvent.setup();
      renderPanel();

      const save = await screen.findByRole("button", { name: "Save" });
      expect(save).toBeDisabled();

      const retention = screen.getByLabelText(/Backups to keep/);
      await user.clear(retention);
      await user.type(retention, "3");

      expect(save).toBeEnabled();
      expect(calls.some((c) => c.method === "PUT")).toBe(false);

      await user.click(save);

      await waitFor(() => {
        const put = calls.find((c) => c.method === "PUT" && c.path === "/backups/settings");
        expect(put?.body).toMatchObject({ retention_count: 3 });
      });
    });

    it("sends null rather than an empty string when the second folder is cleared", async () => {
      const user = userEvent.setup();
      setRoutes(routes({ ...SETTINGS, secondary_dir: "D:/OneDrive/StockSmith" }));
      renderPanel();

      const field = await screen.findByDisplayValue("D:/OneDrive/StockSmith");
      await user.clear(field);
      await user.click(screen.getByRole("button", { name: "Save" }));

      // "" and null mean different things to the backend — blanking the field is an instruction
      // to stop copying, not a no-op.
      await waitFor(() => {
        const put = calls.find((c) => c.method === "PUT" && c.path === "/backups/settings");
        expect((put?.body as Record<string, unknown>).secondary_dir).toBeNull();
      });
    });
  });

  describe("failure surfacing", () => {
    it("warns when the second folder stopped working", async () => {
      setRoutes(
        routes({
          ...SETTINGS,
          secondary_dir: "D:/OneDrive/StockSmith",
          secondary_dir_last_error: "Cannot write to D:/OneDrive/StockSmith: folder not found",
        })
      );
      renderPanel();

      // A synced folder that silently stopped working is how someone ends up believing in
      // off-host copies they don't have.
      expect(await screen.findByText(/Could not copy to that folder last time/)).toBeInTheDocument();
    });

    it("reports a failed scheduled run", async () => {
      setRoutes(routes({ ...SETTINGS, last_run_status: "error", last_run_error: "disk full" }));
      renderPanel();
      expect(await screen.findByText(/Last attempt failed: disk full/)).toBeInTheDocument();
    });

    it("says the settings couldn't load rather than hanging on a spinner", async () => {
      // An unreachable backend used to leave this panel on "Loading…" indefinitely, which reads
      // as a hung app rather than a connection problem.
      setRoutes([{ method: "GET" as const, path: "/backups", respond: () => [] }]);
      renderPanel();

      expect(await screen.findByText(/Couldn't load backup settings|no route for GET/)).toBeInTheDocument();
      expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
    });

    it("explains itself instead of showing controls on an unsupported backend", async () => {
      setRoutes(routes({ ...SETTINGS, supported: false, unsupported_reason: "This backend is on postgresql." }));
      renderPanel();

      expect(await screen.findByText(/This backend is on postgresql/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Back up now" })).not.toBeInTheDocument();
    });
  });

  describe("deleting", () => {
    it("confirms before deleting, without demanding typed confirmation", async () => {
      const user = userEvent.setup();
      renderPanel();

      await user.click(await screen.findByRole("button", { name: "Delete" }));

      const dialog = within(await screen.findByRole("dialog"));
      // One archive, not the data itself — typed confirmation here would just train people to
      // type without reading.
      expect(dialog.queryByRole("textbox")).toBeNull();

      await user.click(dialog.getByRole("button", { name: "Delete backup" }));

      await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    });

    it("does nothing when the confirmation is dismissed", async () => {
      const user = userEvent.setup();
      renderPanel();

      await user.click(await screen.findByRole("button", { name: "Delete" }));
      await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));

      expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    });
  });
});
