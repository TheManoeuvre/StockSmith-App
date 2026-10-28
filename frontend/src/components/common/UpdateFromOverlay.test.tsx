import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const updater = {
  canUpdate: vi.fn(() => true),
  checkForUpdate: vi.fn(),
  installUpdate: vi.fn(),
};
vi.mock("../../lib/updater", () => updater);

const { UpdateFromOverlay } = await import("./UpdateFromOverlay");

describe("UpdateFromOverlay", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updater.canUpdate.mockReturnValue(true);
  });

  it("renders nothing outside the desktop app", () => {
    updater.canUpdate.mockReturnValue(false);
    const { container } = render(<UpdateFromOverlay />);
    expect(container).toBeEmptyDOMElement();
  });

  it("offers to install a newer version found by the check", async () => {
    updater.checkForUpdate.mockResolvedValue({ version: "0.25.1", notes: null, date: null });
    render(<UpdateFromOverlay />);
    await userEvent.click(screen.getByRole("button", { name: "Check for updates" }));
    expect(await screen.findByText("Version 0.25.1 is available.")).toBeInTheDocument();

    updater.installUpdate.mockResolvedValue(undefined);
    await userEvent.click(screen.getByRole("button", { name: "Install and restart" }));
    expect(updater.installUpdate).toHaveBeenCalled();
  });

  it("says so when already up to date", async () => {
    updater.checkForUpdate.mockResolvedValue(null);
    render(<UpdateFromOverlay />);
    await userEvent.click(screen.getByRole("button", { name: "Check for updates" }));
    expect(await screen.findByText("StockSmith is up to date.")).toBeInTheDocument();
  });

  it("shows a failed check and lets the user retry", async () => {
    updater.checkForUpdate.mockRejectedValue(new Error("offline"));
    render(<UpdateFromOverlay />);
    await userEvent.click(screen.getByRole("button", { name: "Check for updates" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
