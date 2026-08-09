import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CopyButton } from "./CopyButton";

function stubClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });
  return writeText;
}

describe("CopyButton", () => {
  it("copies the exact value it was given", async () => {
    const writeText = stubClipboard();
    render(<CopyButton value="SKU-0006-Orange" label="Copy SKU-0006-Orange" />);

    await userEvent.click(screen.getByRole("button", { name: "Copy SKU-0006-Orange" }));

    expect(writeText).toHaveBeenCalledWith("SKU-0006-Orange");
  });

  it("keeps its accessible name while confirming", async () => {
    stubClipboard();
    render(<CopyButton value="DM-1" label="Copy DM-1" />);

    const button = screen.getByRole("button", { name: "Copy DM-1" });
    await userEvent.click(button);

    // The icon flips to a tick, but a label that changed with it would re-announce the
    // button as a different control mid-interaction.
    expect(screen.getByRole("button", { name: "Copy DM-1" })).toBe(button);
  });

  it("falls back to execCommand outside a secure context", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("not allowed"));
    Object.assign(navigator, { clipboard: { writeText } });
    const execCommand = vi.fn().mockReturnValue(true);
    Object.assign(document, { execCommand });

    render(<CopyButton value="DM-2" label="Copy DM-2" />);
    await userEvent.click(screen.getByRole("button", { name: "Copy DM-2" }));

    expect(execCommand).toHaveBeenCalledWith("copy");
  });
});
