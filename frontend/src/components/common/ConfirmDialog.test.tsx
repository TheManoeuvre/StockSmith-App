import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

function setup(overrides: Partial<Parameters<typeof ConfirmDialog>[0]> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  const utils = render(
    <ConfirmDialog
      open
      title="Restore backup"
      body={<p>This replaces everything.</p>}
      confirmLabel="Restore"
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...overrides}
    />
  );
  return { onConfirm, onCancel, ...utils };
}

describe("ConfirmDialog", () => {
  it("renders nothing when closed", () => {
    setup({ open: false });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("confirms without a typed gate", async () => {
    const { onConfirm } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("autofocuses the safe choice, not the destructive one", () => {
    setup();
    // A dialog that opens with the red button focused turns a stray Enter into the action it
    // was put there to prevent.
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel" }));
  });

  describe("typed confirmation", () => {
    it("keeps confirm disabled until the phrase matches exactly", async () => {
      const { onConfirm } = setup({ requireTypedText: "RESTORE" });
      const confirm = screen.getByRole("button", { name: "Restore" });
      expect(confirm).toBeDisabled();

      await userEvent.type(screen.getByRole("textbox"), "RESTOR");
      expect(confirm).toBeDisabled();

      await userEvent.type(screen.getByRole("textbox"), "E");
      expect(confirm).toBeEnabled();

      await userEvent.click(confirm);
      expect(onConfirm).toHaveBeenCalledOnce();
    });

    it("is case-sensitive", async () => {
      setup({ requireTypedText: "RESTORE" });
      await userEvent.type(screen.getByRole("textbox"), "restore");
      expect(screen.getByRole("button", { name: "Restore" })).toBeDisabled();
    });

    it("clears the typed phrase when reopened", async () => {
      const { rerender, onConfirm } = setup({ requireTypedText: "RESTORE" });
      await userEvent.type(screen.getByRole("textbox"), "RESTORE");
      expect(screen.getByRole("button", { name: "Restore" })).toBeEnabled();

      // Cancel, then open again. The gate must re-arm — otherwise the second showing is a
      // single click away from firing, which defeats the whole point of the pause.
      rerender(
        <ConfirmDialog
          open={false}
          title="Restore backup"
          body={<p>This replaces everything.</p>}
          confirmLabel="Restore"
          requireTypedText="RESTORE"
          onConfirm={onConfirm}
          onCancel={vi.fn()}
        />
      );
      rerender(
        <ConfirmDialog
          open
          title="Restore backup"
          body={<p>This replaces everything.</p>}
          confirmLabel="Restore"
          requireTypedText="RESTORE"
          onConfirm={onConfirm}
          onCancel={vi.fn()}
        />
      );

      expect(screen.getByRole("textbox")).toHaveValue("");
      expect(screen.getByRole("button", { name: "Restore" })).toBeDisabled();
    });
  });

  describe("while busy", () => {
    it("disables both buttons so the action can't be fired twice", () => {
      setup({ busy: true });
      expect(screen.getByRole("button", { name: "Working…" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    });

    it("ignores Escape", async () => {
      const { onCancel } = setup({ busy: true });
      await userEvent.keyboard("{Escape}");
      // The work is already in flight; dismissing the dialog would hide it, not stop it.
      expect(onCancel).not.toHaveBeenCalled();
    });
  });

  it("dismisses on Escape when idle", async () => {
    const { onCancel, onConfirm } = setup();
    await userEvent.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledOnce();
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
