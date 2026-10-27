import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

const tauri = vi.hoisted(() => ({
  ignored: null as string | null,
  offered: null as { version: string; notes: string | null; date: string | null } | null,
  installUpdate: vi.fn(),
  openExternalUrl: vi.fn(),
}));

vi.mock("../../lib/tauri", () => ({
  openExternalUrl: tauri.openExternalUrl,
}));
vi.mock("../../lib/updater", () => ({
  canUpdate: () => true,
  getInstalledVersion: () => Promise.resolve("0.24.0"),
  checkForUpdate: () => Promise.resolve(tauri.offered),
  getIgnoredUpdateVersion: () => Promise.resolve(tauri.ignored),
  setIgnoredUpdateVersion: (v: string | null) => {
    tauri.ignored = v;
    return Promise.resolve();
  },
  installUpdate: tauri.installUpdate,
}));

const { AboutSettings } = await import("./AboutSettings");

const NOTES = "### Added\n\n- **Dark mode.** Easier on the eyes.";

function renderAbout() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AboutSettings />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  tauri.ignored = null;
  tauri.offered = null;
  tauri.installUpdate.mockReset();
  tauri.openExternalUrl.mockReset();
});

it("shows the installed version and says so when up to date", async () => {
  renderAbout();
  expect(await screen.findByText("0.24.0")).toBeInTheDocument();
  expect(await screen.findByText("StockSmith is up to date.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /install/i })).not.toBeInTheDocument();
});

it("offers the newest release's notes with a link to the full history", async () => {
  tauri.offered = { version: "0.26.0", notes: NOTES, date: null };
  renderAbout();
  expect(await screen.findByText("Version 0.26.0 is available.")).toBeInTheDocument();
  expect(screen.getByText("Dark mode.")).toBeInTheDocument();
  expect(screen.getByText(/These notes cover 0\.26\.0 only — you're on 0\.24\.0/)).toBeInTheDocument();

  await userEvent.click(screen.getByRole("link", { name: "Full release history on GitHub" }));
  expect(tauri.openExternalUrl).toHaveBeenCalledWith("https://github.com/TheManoeuvre/StockSmith-App/releases");
});

it("installs on request", async () => {
  tauri.offered = { version: "0.26.0", notes: NOTES, date: null };
  tauri.installUpdate.mockResolvedValue(undefined);
  renderAbout();
  await userEvent.click(await screen.findByRole("button", { name: "Install and restart" }));
  expect(tauri.installUpdate).toHaveBeenCalled();
});

it("reports an install that fails and offers it again", async () => {
  tauri.offered = { version: "0.26.0", notes: NOTES, date: null };
  tauri.installUpdate.mockRejectedValue(new Error("signature mismatch"));
  renderAbout();
  await userEvent.click(await screen.findByRole("button", { name: "Install and restart" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("signature mismatch");
  expect(screen.getByRole("button", { name: "Install and restart" })).toBeInTheDocument();
});

it("ignoring a release hides the call to action but keeps it installable", async () => {
  tauri.offered = { version: "0.26.0", notes: NOTES, date: null };
  renderAbout();
  await userEvent.click(await screen.findByRole("button", { name: "Ignore this version" }));

  expect(await screen.findByText(/you've chosen to ignore it/)).toBeInTheDocument();
  expect(tauri.ignored).toBe("0.26.0");
  expect(screen.getByRole("button", { name: "Install anyway" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Stop ignoring" }));
  expect(await screen.findByText("Version 0.26.0 is available.")).toBeInTheDocument();
  expect(tauri.ignored).toBeNull();
});

it("a release newer than the ignored one is offered again", async () => {
  tauri.ignored = "0.25.0";
  tauri.offered = { version: "0.26.0", notes: NOTES, date: null };
  renderAbout();
  expect(await screen.findByText("Version 0.26.0 is available.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ignore this version" })).toBeInTheDocument();
});

it("lists third-party licences when opened", async () => {
  renderAbout();
  await userEvent.click(await screen.findByRole("button", { name: /Show licences/ }));
  expect(await screen.findByText(/Frontend \(npm\)/)).toBeInTheDocument();
  expect(screen.getByText("react")).toBeInTheDocument();
});
