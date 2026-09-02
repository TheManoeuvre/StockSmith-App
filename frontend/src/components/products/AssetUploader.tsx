import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { assetsApi } from "../../api/assets";
import { assetDownloadUrl, platformFetch } from "../../api/client";
import { pickFile } from "../../lib/tauri";
import { useAssetUrl } from "../../hooks/useAssetUrl";
import { useLazyVisible } from "../../hooks/useLazyVisible";
import type { AssetType } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";
import { useEditableCopy } from "../../hooks/useEditableCopy";

/** Fetches an asset's original bytes with auth and hands the viewer a save dialog — works
 *  in the Tauri webview where a plain <a href> to a Bearer-guarded URL can't. */
function DownloadButton({
  assetId,
  filename,
  className,
}: {
  assetId: number;
  filename: string;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const download = async () => {
    setBusy(true);
    try {
      const { url, headers } = await assetDownloadUrl(assetId);
      const res = await platformFetch(url, { headers });
      if (!res.ok) return;
      const objectUrl = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(objectUrl);
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      type="button"
      onClick={download}
      disabled={busy}
      className={className ?? "text-xs text-slate-600 underline disabled:opacity-50"}
    >
      {busy ? "…" : "Download"}
    </button>
  );
}

const SECTIONS: { type: AssetType; label: string }[] = [
  { type: "listing_image", label: "Listing images" },
  { type: "step", label: "STEP files" },
  { type: "threemf", label: "3MF files" },
  { type: "gcode", label: "GCODE files" },
];

export function AssetUploader({ productId }: { productId: number }) {
  const queryClient = useQueryClient();
  const { data: assets } = useQuery({ queryKey: ["products", productId, "assets"], queryFn: () => assetsApi.list(productId) });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["products", productId, "assets"] });

  const uploadMutation = useMutation({
    mutationFn: ({ assetType }: { assetType: AssetType }) =>
      pickFile().then((picked) => {
        if (!picked) return;
        return assetsApi.upload(productId, picked.path, picked.name, assetType);
      }),
    onSuccess: invalidate,
  });

  const importUrlMutation = useMutation({
    mutationFn: ({ assetType, url }: { assetType: AssetType; url: string }) =>
      assetsApi.importUrl(productId, url, assetType),
    onSuccess: invalidate,
  });

  const removeMutation = useMutation({
    mutationFn: (assetId: number) => assetsApi.remove(assetId),
    onSuccess: invalidate,
  });

  return (
    <div className="flex flex-col gap-4">
      <h3 className="text-md font-semibold">
        Assets · {assets?.length ?? 0} {(assets?.length ?? 0) === 1 ? "file" : "files"}
      </h3>
      <ErrorBanner error={uploadMutation.error ?? removeMutation.error ?? importUrlMutation.error} />
      {SECTIONS.map((section) => (
        <AssetSection
          key={section.type}
          label={section.label}
          assetType={section.type}
          assets={assets?.filter((a) => a.asset_type === section.type) ?? []}
          onUpload={() => uploadMutation.mutate({ assetType: section.type })}
          onImportUrl={(url) => importUrlMutation.mutate({ assetType: section.type, url })}
          onRemove={(assetId) => removeMutation.mutate(assetId)}
        />
      ))}
    </div>
  );
}

function AssetSection({
  label,
  assetType,
  assets,
  onUpload,
  onImportUrl,
  onRemove,
}: {
  label: string;
  assetType: AssetType;
  assets: {
    id: number;
    original_filename: string;
    width_px: number | null;
    height_px: number | null;
  }[];
  onUpload: () => void;
  onImportUrl: (url: string) => void;
  onRemove: (assetId: number) => void;
}) {
  // A submit-and-clear command form, so it diffs against "" rather than server data — enough
  // to warn about a pasted-but-not-imported URL on navigate-away.
  const { value: url, setValue: setUrl, markSaved: markImported } = useEditableCopy<string>({
    key: `assets/${assetType}`,
    label: `${label} image URL`,
    initial: "",
    seed: "",
    seedKey: "const",
  });
  const [isDragOver, setIsDragOver] = useState(false);

  return (
    <div
      className={`rounded bg-white p-3 shadow-sm ${isDragOver ? "ring-2 ring-slate-400" : ""}`}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("text/uri-list")) {
          e.preventDefault();
          setIsDragOver(true);
        }
      }}
      onDragLeave={() => setIsDragOver(false)}
      onDrop={(e) => {
        const droppedUrl = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
        if (droppedUrl) {
          e.preventDefault();
          onImportUrl(droppedUrl);
        }
        setIsDragOver(false);
      }}
    >
      <div className="mb-2 flex items-center justify-between">
        <h3 className="font-medium">{label}</h3>
        <button onClick={onUpload} className="rounded border border-slate-300 px-3 py-1 text-sm">
          Upload
        </button>
      </div>
      <form
        className="mb-2 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (url.trim()) {
            onImportUrl(url.trim());
            markImported("");
          }
        }}
      >
        <input
          className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
          placeholder="Paste image URL, or drag a link here…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button
          type="submit"
          disabled={!url.trim()}
          className="rounded border border-slate-300 px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50"
        >
          Import
        </button>
      </form>
      {assetType.includes("image") ? (
        <div className="flex flex-wrap gap-3">
          {assets.map((asset) => (
            <AssetThumb
              key={asset.id}
              assetId={asset.id}
              filename={asset.original_filename}
              dimensions={
                asset.width_px && asset.height_px
                  ? `${asset.width_px}×${asset.height_px}`
                  : null
              }
              isImage
              onRemove={() => onRemove(asset.id)}
            />
          ))}
        </div>
      ) : (
        assets.length > 0 && (
          <ul className="divide-y divide-slate-100 text-sm">
            {assets.map((asset) => (
              <li key={asset.id} className="flex items-center gap-3 py-1.5">
                <span className="min-w-0 flex-1 truncate">
                  {asset.original_filename}
                </span>
                <span className="shrink-0 text-xs tabular-nums text-slate-400">
                  {asset.width_px && asset.height_px
                    ? `${asset.width_px}×${asset.height_px}`
                    : "—"}
                </span>
                <DownloadButton
                  assetId={asset.id}
                  filename={asset.original_filename}
                />
                <button
                  type="button"
                  onClick={() => onRemove(asset.id)}
                  className="shrink-0 text-xs text-red-600"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )
      )}
    </div>
  );
}

function AssetThumb({
  assetId,
  filename,
  dimensions,
  isImage,
  onRemove,
}: {
  assetId: number;
  filename: string;
  dimensions: string | null;
  isImage: boolean;
  onRemove: () => void;
}) {
  const [wantsPreview, setWantsPreview] = useState(isImage);
  const ref = useRef<HTMLDivElement>(null);
  const isVisible = useLazyVisible(ref);
  const blobUrl = useAssetUrl(wantsPreview && isVisible ? assetId : null);

  return (
    <div ref={ref} className="relative w-24 rounded border border-slate-200 p-1 text-center">
      {isImage && blobUrl ? (
        <img src={blobUrl} alt={filename} className="h-20 w-full object-cover" />
      ) : (
        <div
          className="flex h-20 w-full items-center justify-center bg-slate-100 text-xs"
          onClick={() => setWantsPreview(true)}
        >
          {filename}
        </div>
      )}
      <p className="truncate text-xs">{filename}</p>
      {dimensions && (
        <p className="text-[10px] tabular-nums text-slate-400">{dimensions}</p>
      )}
      <div className="flex items-center justify-center gap-2">
        <DownloadButton assetId={assetId} filename={filename} />
        <button onClick={onRemove} className="text-xs text-red-600">
          Remove
        </button>
      </div>
    </div>
  );
}
