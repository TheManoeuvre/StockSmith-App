import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", async () => (await import("../../test/fakeBackend")).clientMock());

const tauri = {
  isDesktopApp: vi.fn(() => true),
  getAutostartEnabled: vi.fn(() => Promise.resolve(false)),
  setAutostartEnabled: vi.fn((enabled: boolean) => Promise.resolve(enabled)),
};
vi.mock("../../lib/tauri", () => tauri);

const { setRoutes } = await import("../../test/fakeBackend");
const { BackgroundSyncSettings } = await import("./BackgroundSyncSettings");

const HEALTHY = {
  window_days: 7,
  measurable: true,
  reason: null,
  expected_interval_minutes: 15,
  gap_threshold_minutes: 30,
  gaps: [],
  total_gap_minutes: 0,
  longest_gap_minutes: 0,
};

function withHealth(health: Record<string, unknown>) {
  setRoutes([{ method: "GET" as const, path: /^\/platforms\/sync-health/, respond: () => health }]);
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <BackgroundSyncSettings />
    </QueryClientProvider>
  );
}

describe("BackgroundSyncSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    tauri.isDesktopApp.mockReturnValue(true);
    tauri.getAutostartEnabled.mockResolvedValue(false);
    tauri.setAutostartEnabled.mockImplementation((enabled: boolean) => Promise.resolve(enabled));
    withHealth(HEALTHY);
  });

  it("reports clean coverage without listing anything", async () => {
    renderPanel();
    expect(await screen.findByText(/no gaps/i)).toBeInTheDocument();
  });

  it("says why it can't tell rather than implying perfect uptime", async () => {
    // The distinction the backend goes to trouble to preserve: no gaps and no basis for an
    // answer must not render the same, or "auto-sync is off" reads as "everything is fine".
    withHealth({
      ...HEALTHY,
      measurable: false,
      reason: "No marketplace has auto-sync turned on.",
    });
    renderPanel();

    expect(await screen.findByText("No marketplace has auto-sync turned on.")).toBeInTheDocument();
    expect(screen.queryByText(/no gaps/i)).not.toBeInTheDocument();
  });

  it("summarises gaps and leads with the longest", async () => {
    withHealth({
      ...HEALTHY,
      gaps: [
        { started_at: "2026-08-14T01:00:00Z", ended_at: "2026-08-14T01:45:00Z", minutes: 45 },
        { started_at: "2026-08-13T02:00:00Z", ended_at: "2026-08-13T08:00:00Z", minutes: 360 },
      ],
      total_gap_minutes: 405,
      longest_gap_minutes: 360,
    });
    renderPanel();

    expect(await screen.findByText(/2 gaps totalling 6h 45m, longest 6h/)).toBeInTheDocument();
    // Longest first: on a bad week the six-hour outage is the story, not the 45-minute one.
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("(6h)");
    expect(items[1]).toHaveTextContent("(45m)");
  });

  it("turns autostart on through Windows and reflects what it reports back", async () => {
    renderPanel();
    const toggle = await screen.findByRole("checkbox");
    await waitFor(() => expect(toggle).not.toBeDisabled());

    await userEvent.click(toggle);

    expect(tauri.setAutostartEnabled).toHaveBeenCalledWith(true);
    await waitFor(() => expect(toggle).toBeChecked());
  });

  it("warns when Windows didn't keep the setting", async () => {
    // plugins-workspace#771: the registry entry can disappear. A toggle that reported
    // success anyway would be lying in precisely the case worth catching.
    tauri.setAutostartEnabled.mockResolvedValue(false);
    renderPanel();
    const toggle = await screen.findByRole("checkbox");
    await waitFor(() => expect(toggle).not.toBeDisabled());

    await userEvent.click(toggle);

    expect(await screen.findByText(/didn't keep that setting/i)).toBeInTheDocument();
    expect(toggle).not.toBeChecked();
  });

  it("hides the autostart toggle outside the desktop app", async () => {
    // A browser preview has no Windows to start with, and a toggle that silently does
    // nothing is worse than one that isn't offered.
    tauri.isDesktopApp.mockReturnValue(false);
    renderPanel();

    await screen.findByText(/no gaps/i);
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
