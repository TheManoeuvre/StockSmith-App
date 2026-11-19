import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { workspaceApi, type WorkspaceMode } from "../../api/workspace";
import { restartApp } from "../../lib/tauri";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { ErrorBanner } from "../common/ErrorBanner";

/**
 * The sample workspace — a separate, throwaway database of an invented candle shop
 * (backend/app/services/sample_data.py). Switching records a request with the backend and
 * restarts the app, which opens the other database at start-up (backend/app/bootstrap.py).
 */
export function useWorkspace() {
  return useQuery({ queryKey: ["workspace"], queryFn: workspaceApi.get, staleTime: Infinity });
}

function useSwitch() {
  return useMutation({
    mutationFn: async (mode: WorkspaceMode) => {
      await workspaceApi.switchTo(mode);
      await restartApp();
    },
  });
}

/** Across the top of every page while the sample is open, so it can't be mistaken for real data. */
export function SampleWorkspaceBanner() {
  const { data } = useWorkspace();
  const leave = useSwitch();
  const [confirming, setConfirming] = useState(false);
  if (data?.mode !== "sample") return null;
  return (
    <div role="status" className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-300 bg-amber-50 px-6 py-2 text-sm text-amber-900">
      <span>
        <strong>Sample data:</strong> you're exploring {data.sample_business}. Nothing here is real — change anything you
        like. Your own StockSmith isn't syncing while you look around.
      </span>
      <button
        type="button"
        onClick={() => setConfirming(true)}
        className="rounded-md border border-amber-400 bg-white px-2.5 py-1 text-xs font-medium hover:bg-amber-100"
      >
        Leave sample data
      </button>
      <ConfirmDialog
        open={confirming}
        title="Leave the sample data?"
        tone="default"
        body={
          <p>
            StockSmith will restart with your own data. The sample is deleted, including anything you changed in it. You
            can open a fresh one again from Help.
          </p>
        }
        confirmLabel="Leave and restart"
        busy={leave.isPending}
        onConfirm={() => leave.mutate("real")}
        onCancel={() => setConfirming(false)}
      />
      {leave.error && <ErrorBanner error={leave.error} />}
    </div>
  );
}

/** "Try it with sample data" — on the first-launch screen and Help's home page. */
export function ExploreSampleButton({ variant = "link" }: { variant?: "link" | "card" }) {
  const { data } = useWorkspace();
  const enter = useSwitch();
  if (!data?.can_switch || data.mode !== "real") return null;
  const button = (
    <button
      type="button"
      onClick={() => enter.mutate("sample")}
      disabled={enter.isPending}
      className={
        variant === "card"
          ? "rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-900 hover:bg-slate-50"
          : "text-sm text-slate-600 underline hover:text-slate-900"
      }
    >
      {enter.isPending ? "Opening sample data…" : "Look around with sample data first"}
    </button>
  );
  if (variant === "link") {
    return (
      <span className="flex flex-col items-start gap-1">
        {button}
        <ErrorBanner error={enter.error} />
      </span>
    );
  }
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm">
      <span className="text-slate-600">
        Try everything on an invented candle shop — products, orders, stock and margins — without touching your own data.
      </span>
      {button}
      <ErrorBanner error={enter.error} />
    </div>
  );
}
