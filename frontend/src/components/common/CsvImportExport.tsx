import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import type { CsvImportResult } from "../../api/client";
import { ErrorBanner } from "./ErrorBanner";

export function CsvImportExport({
  onExport,
  onImport,
  invalidateKey,
  className = "flex flex-col gap-2",
}: {
  onExport: () => Promise<void>;
  onImport: (fileBytes: Uint8Array, filename: string) => Promise<CsvImportResult>;
  invalidateKey: string | string[];
  /** Wrapper layout. Defaults to a stacked block; pass `"contents"` to let the two buttons
   *  sit directly in a parent toolbar flex row (the import-result panel then flows after it
   *  as the next flex child). */
  className?: string;
}) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const exportMutation = useMutation({ mutationFn: onExport });

  const importMutation = useMutation({
    mutationFn: async (file: File) => {
      const bytes = new Uint8Array(await file.arrayBuffer());
      return onImport(bytes, file.name);
    },
    onSuccess: () => {
      for (const key of Array.isArray(invalidateKey) ? invalidateKey : [invalidateKey]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });

  return (
    <div className={className}>
      <div className="flex gap-2">
        <button
          onClick={() => exportMutation.mutate()}
          className="rounded border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50"
        >
          Export CSV
        </button>
        <button
          onClick={() => fileInputRef.current?.click()}
          className="rounded border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50"
        >
          Import CSV
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) importMutation.mutate(file);
            e.target.value = "";
          }}
        />
      </div>
      <ErrorBanner error={exportMutation.error ?? importMutation.error} />
      {importMutation.data && (
        <div className="rounded bg-white p-3 text-sm shadow-sm">
          <p>
            Created <strong>{importMutation.data.created}</strong>, updated{" "}
            <strong>{importMutation.data.updated}</strong>
            {importMutation.data.failed.length > 0 && (
              <>
                , failed <strong>{importMutation.data.failed.length}</strong>
              </>
            )}
            .
          </p>
          {importMutation.data.failed.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-red-600">
              {importMutation.data.failed.map((f, i) => (
                <li key={i}>
                  Row {f.row}: {f.error}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
