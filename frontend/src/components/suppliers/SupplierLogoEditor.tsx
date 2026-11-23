import { useMutation, useQueryClient } from "@tanstack/react-query";
import { suppliersApi } from "../../api/suppliers";
import type { Supplier } from "../../api/types";
import { pickFile } from "../../lib/tauri";
import { ErrorBanner } from "../common/ErrorBanner";
import { SupplierLogo } from "./SupplierLogo";

/**
 * The logo controls inside an expanded supplier row. Acts on the saved supplier immediately —
 * these are commands, not form fields, so they don't join the row's Save/dirty tracking.
 *
 * Precedence is the thing to communicate: an uploaded logo overrides the website's icon, and
 * removing the upload falls back to the icon.
 */
export function SupplierLogoEditor({ supplier }: { supplier: Supplier }) {
  const queryClient = useQueryClient();
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["suppliers"] });
    // Purchases carry the logo stamp, so a cached purchase would keep showing the old one.
    queryClient.invalidateQueries({ queryKey: ["purchases"] });
  };

  const upload = useMutation({
    mutationFn: async () => {
      const picked = await pickFile();
      if (picked) await suppliersApi.uploadLogo(supplier.id, picked.path);
    },
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: () => suppliersApi.removeLogo(supplier.id), onSuccess: refresh });
  const fetchIcon = useMutation({ mutationFn: () => suppliersApi.refreshFavicon(supplier.id), onSuccess: refresh });

  const busy = upload.isPending || remove.isPending || fetchIcon.isPending;
  const hasLogo = supplier.logo_updated_at !== null;
  const buttonClass = "rounded border border-slate-300 bg-white px-2 py-1 text-sm disabled:opacity-50";

  return (
    <div className="flex flex-col gap-2 text-sm">
      <span>Logo</span>
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded border border-slate-200 bg-white">
          {hasLogo ? (
            <SupplierLogo supplierId={supplier.id} updatedAt={supplier.logo_updated_at} className="h-12 w-12" />
          ) : (
            <span className="text-[11px] text-slate-400">None</span>
          )}
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs text-slate-500">
            {supplier.logo_source === "upload"
              ? "Uploaded logo — overrides the website's icon."
              : supplier.logo_source === "favicon"
                ? "Icon fetched from the website."
                : supplier.website_url
                  ? "No icon found on the website. Upload a logo instead."
                  : "Add a website to fetch its icon, or upload a logo."}
          </span>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={buttonClass} disabled={busy} onClick={() => upload.mutate()}>
              {supplier.logo_source === "upload" ? "Replace logo…" : "Upload logo…"}
            </button>
            {supplier.logo_source === "upload" && (
              <button type="button" className={buttonClass} disabled={busy} onClick={() => remove.mutate()}>
                {supplier.website_url ? "Use website icon" : "Remove logo"}
              </button>
            )}
            {supplier.logo_source !== "upload" && supplier.website_url && (
              <button type="button" className={buttonClass} disabled={busy} onClick={() => fetchIcon.mutate()}>
                {hasLogo ? "Refresh icon" : "Fetch icon"}
              </button>
            )}
          </div>
        </div>
      </div>
      <ErrorBanner error={upload.error ?? remove.error ?? fetchIcon.error} />
    </div>
  );
}
