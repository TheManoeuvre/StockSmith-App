import { useEffect, useRef } from "react";
import { useDirtyRegistryApi } from "./useDirtyRegistry";

// Checked when the effect runs rather than captured at module load: a module-level const
// bakes in whatever was true at import time, which is a hidden dependency on load order and
// makes the hook impossible to exercise in a test.
const isTauri = () => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/**
 * Asks before the desktop window closes on unsaved work.
 *
 * `enableBeforeUnload` on the router blocker covers a browser tab, but not this: the Tauri
 * webview never fires `beforeunload` for a window close, and the Rust side used to handle
 * CloseRequested without calling `prevent_close`, so the window went regardless.
 *
 * `onCloseRequested` must call `preventDefault()` synchronously — the decision can't be
 * awaited — so this always cancels the close first and then asks. `onConfirmClose` shows the
 * dialog; if the user discards, it calls the returned `destroy` to actually close.
 *
 * A no-op outside Tauri, where the browser's own beforeunload prompt already applies.
 */
export function useTauriCloseGuard(onConfirmClose: (destroy: () => void) => void): void {
  // Kept in a ref so the listener is registered once: re-subscribing on every render would
  // race the unlisten and can leave a window that no longer prompts at all.
  const handlerRef = useRef(onConfirmClose);
  handlerRef.current = onConfirmClose;

  const registry = useDirtyRegistryApi();

  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;

    (async () => {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      const appWindow = getCurrentWindow();
      const stop = await appWindow.onCloseRequested((event) => {
        if (!registry.isDirtyUnder("")) return; // nothing unsaved — let it close
        event.preventDefault();
        // Cancelling the close is only defensible if something then appears to explain it.
        // If the prompt throws — a crashed render tree, a guard in a bad state — the window
        // has been stopped from closing with nothing on screen, which is indistinguishable
        // from a dead title-bar button and leaves no way out but Task Manager. Losing
        // unsaved work is bad; trapping someone in a window they cannot close is worse, and
        // unlike lost work it gives them nowhere to go. So: fail open.
        try {
          handlerRef.current(() => void appWindow.destroy());
        } catch {
          void appWindow.destroy();
        }
      });
      if (cancelled) stop();
      else unlisten = stop;
    })();

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [registry]);
}
