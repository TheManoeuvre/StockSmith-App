import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { stockTakesApi } from "../../api/stockTakes";
import type { StockTakeImportResult } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { Modal } from "../common/Modal";

/**
 * Export and import for one take's count sheet.
 *
 * The generic CsvImportExport can't be reused: it applies an upload in one shot, and a
 * count sheet needs a look first. Rows that fail validation here aren't a nuisance to
 * report afterwards — they're counts that won't land, and finding that out after the fact
 * means not knowing which shelf still needs walking.
 *
 * So the file is uploaded twice: once as a dry run to build this preview, then again to
 * apply once the choice is made. The server writes nothing on the first pass.
 */
export function StockTakeCsvPanel({ stockTakeId, onImported }: { stockTakeId: number; onImported: () => void }) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Held so the second upload sends exactly the bytes that were previewed, rather than
  // re-reading a file that could have changed on disk in between.
  const [pending, setPending] = useState<{ bytes: Uint8Array; filename: string; result: StockTakeImportResult } | null>(
    null,
  );

  const exportMutation = useMutation({ mutationFn: () => stockTakesApi.exportCsv(stockTakeId) });

  const previewMutation = useMutation({
    mutationFn: async (file: File) => {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const result = await stockTakesApi.importCsv(stockTakeId, bytes, file.name, {
        dryRun: true,
        onError: "skip",
      });
      return { bytes, filename: file.name, result };
    },
    onSuccess: setPending,
  });

  const applyMutation = useMutation({
    mutationFn: (onError: "skip" | "fail") =>
      stockTakesApi.importCsv(stockTakeId, pending!.bytes, pending!.filename, { dryRun: false, onError }),
    onSuccess: () => {
      setPending(null);
      onImported();
    },
  });

  const failed = pending?.result.failed ?? [];

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2">
        <button onClick={() => exportMutation.mutate()} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
          Export count sheet
        </button>
        <button onClick={() => fileInputRef.current?.click()} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
          Import counts
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) previewMutation.mutate(file);
            // Reset so picking the same file again still fires a change event.
            e.target.value = "";
          }}
        />
      </div>
      <ErrorBanner error={exportMutation.error ?? previewMutation.error ?? applyMutation.error} />

      {/* Rendered only when there is something to confirm — Modal has no `open` prop; it
          shows whenever it's mounted. */}
      {pending && (
      <Modal
        title="Check before applying"
        onClose={() => setPending(null)}
        footer={
          <div className="flex gap-2">
            <button
              onClick={() => applyMutation.mutate("skip")}
              disabled={applyMutation.isPending}
              className="rounded bg-slate-900 px-4 py-1.5 text-white disabled:opacity-50"
            >
              Apply {pending?.result.matched ?? 0} count{pending?.result.matched === 1 ? "" : "s"}
              {failed.length > 0 && `, skip ${failed.length}`}
            </button>
            {failed.length > 0 && (
              <button
                onClick={() => applyMutation.mutate("fail")}
                disabled={applyMutation.isPending}
                className="rounded border border-slate-300 px-4 py-1.5 text-sm"
              >
                Apply nothing — I'll fix the file
              </button>
            )}
            <button onClick={() => setPending(null)} className="rounded border border-slate-300 px-4 py-1.5 text-sm">
              Cancel
            </button>
          </div>
        }
      >
        <div className="flex flex-col gap-2 text-sm">
          <p>
            <strong>{pending?.result.matched ?? 0}</strong> count{pending?.result.matched === 1 ? "" : "s"} ready to
            apply
            {(pending?.result.skipped_blank ?? 0) > 0 && (
              <>
                , <strong>{pending?.result.skipped_blank}</strong> row
                {pending?.result.skipped_blank === 1 ? "" : "s"} left blank
              </>
            )}
            {failed.length > 0 && (
              <>
                , <strong className="text-red-600">{failed.length}</strong> couldn't be read
              </>
            )}
            .
          </p>
          {(pending?.result.skipped_blank ?? 0) > 0 && (
            <p className="text-slate-500">
              Blank rows are left exactly as they were — they aren't treated as a count of zero.
            </p>
          )}
          {failed.length > 0 && (
            <div>
              <p className="mb-1 font-medium text-red-600">Rows that couldn't be read</p>
              <ul className="list-disc pl-5 text-red-600">
                {failed.slice(0, 20).map((f) => (
                  <li key={f.row}>
                    Row {f.row}: {f.error}
                  </li>
                ))}
              </ul>
              {failed.length > 20 && <p className="text-slate-500">…and {failed.length - 20} more.</p>}
            </div>
          )}
          <p className="text-slate-500">Nothing has been saved yet.</p>
        </div>
      </Modal>
      )}
    </div>
  );
}
