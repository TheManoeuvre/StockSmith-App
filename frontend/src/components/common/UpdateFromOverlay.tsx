import { useState } from "react";
import { canUpdate, checkForUpdate, installUpdate, type AvailableUpdate } from "../../lib/updater";

type Phase =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "current" }
  | { kind: "available"; update: AvailableUpdate }
  | { kind: "installing"; percent: number | null }
  | { kind: "error"; message: string };

const BUTTON = "h-8 rounded-md bg-slate-900 px-3 text-xs font-semibold text-white disabled:opacity-50";

/**
 * Check for and install an update from the "Reconnecting" overlay.
 *
 * The updater runs in the desktop shell, not the backend, so it works exactly when it is needed
 * most: a release that broke the backend can be replaced without the app ever having loaded. A
 * stuck "Reconnecting" screen used to cover the sidebar and everything else, leaving no way to
 * reach Settings > About.
 */
export function UpdateFromOverlay() {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  if (!canUpdate()) return null;

  async function check() {
    setPhase({ kind: "checking" });
    try {
      const update = await checkForUpdate();
      setPhase(update ? { kind: "available", update } : { kind: "current" });
    } catch (error) {
      setPhase({ kind: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }

  async function install() {
    setPhase({ kind: "installing", percent: null });
    try {
      await installUpdate((loaded, total) =>
        setPhase({ kind: "installing", percent: total ? Math.min(100, Math.round((loaded / total) * 100)) : null })
      );
    } catch (error) {
      setPhase({ kind: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }

  return (
    <div className="mt-2 flex flex-col items-center gap-2 border-t border-slate-200 pt-3 text-sm text-slate-600">
      {phase.kind === "idle" && (
        <>
          <p className="text-xs">Stuck here after an update? A newer version may fix it.</p>
          <button type="button" onClick={check} className={BUTTON}>
            Check for updates
          </button>
        </>
      )}
      {phase.kind === "checking" && <p role="status">Checking for updates…</p>}
      {phase.kind === "current" && <p role="status">StockSmith is up to date.</p>}
      {phase.kind === "available" && (
        <>
          <p role="status">Version {phase.update.version} is available.</p>
          <button type="button" onClick={install} className={BUTTON}>
            Install and restart
          </button>
        </>
      )}
      {phase.kind === "installing" && (
        <p role="status">
          {phase.percent === null || phase.percent < 100
            ? `Downloading… ${phase.percent !== null ? `${phase.percent}%` : ""}`
            : "Installing — StockSmith will restart."}
        </p>
      )}
      {phase.kind === "error" && (
        <>
          <p role="alert" className="text-xs text-red-700">
            {phase.message}
          </p>
          <button type="button" onClick={check} className={BUTTON}>
            Try again
          </button>
        </>
      )}
    </div>
  );
}
