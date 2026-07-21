import type { SaveStatus } from "../../hooks/useSaveStatus";

export function SaveIndicator({ status }: { status: SaveStatus }) {
  if (status === "saving") return <span className="text-sm text-slate-500">Saving…</span>;
  if (status === "saved") return <span className="text-sm text-green-600">Saved ✓</span>;
  return null;
}
