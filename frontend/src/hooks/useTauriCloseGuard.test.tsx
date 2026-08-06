import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DirtyPath, DirtyRegistryProvider, useDirtyRegistration } from "./useDirtyRegistry";
import { useTauriCloseGuard } from "./useTauriCloseGuard";

const destroy = vi.fn();
const onCloseRequested = vi.fn();

vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({
    destroy,
    onCloseRequested: (handler: (e: { preventDefault: () => void }) => void) => {
      onCloseRequested(handler);
      return Promise.resolve(() => {});
    },
  }),
}));

/** The hook no-ops unless it believes it's inside a Tauri webview. */
function pretendTauri() {
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
}

afterEach(() => {
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
  vi.clearAllMocks();
  vi.resetModules();
});

function Editor({ dirty }: { dirty: boolean }) {
  useDirtyRegistration("editor", "Some editor", dirty);
  return null;
}

function Harness({ dirty, onConfirm }: { dirty: boolean; onConfirm: (d: () => void) => void }) {
  useTauriCloseGuard(onConfirm);
  return (
    <DirtyPath segment="page">
      <Editor dirty={dirty} />
    </DirtyPath>
  );
}

async function mount(dirty: boolean, onConfirm: (d: () => void) => void) {
  render(
    <DirtyRegistryProvider>
      <Harness dirty={dirty} onConfirm={onConfirm} />
    </DirtyRegistryProvider>
  );
  // The listener is registered from an async effect (dynamic import of the Tauri API).
  await vi.waitFor(() => expect(onCloseRequested).toHaveBeenCalled());
  return onCloseRequested.mock.calls[0][0] as (e: { preventDefault: () => void }) => void;
}

describe("useTauriCloseGuard", () => {
  it("does nothing outside Tauri — the browser's own prompt applies there", () => {
    render(
      <DirtyRegistryProvider>
        <Harness dirty onConfirm={() => {}} />
      </DirtyRegistryProvider>
    );
    expect(onCloseRequested).not.toHaveBeenCalled();
  });

  it("lets a clean window close without asking", async () => {
    pretendTauri();
    const onConfirm = vi.fn();
    const handler = await mount(false, onConfirm);

    const preventDefault = vi.fn();
    handler({ preventDefault });

    expect(preventDefault).not.toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("cancels the close and asks when something is unsaved", async () => {
    pretendTauri();
    const onConfirm = vi.fn();
    const handler = await mount(true, onConfirm);

    const preventDefault = vi.fn();
    handler({ preventDefault });

    // preventDefault has to happen synchronously — the decision can't be awaited here.
    expect(preventDefault).toHaveBeenCalled();
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(destroy).not.toHaveBeenCalled();
  });

  it("closes the window when the caller discards", async () => {
    pretendTauri();
    let discard: (() => void) | undefined;
    const handler = await mount(true, (d) => (discard = d));

    handler({ preventDefault: vi.fn() });
    expect(destroy).not.toHaveBeenCalled();

    discard!();
    expect(destroy).toHaveBeenCalledTimes(1);
  });
});
