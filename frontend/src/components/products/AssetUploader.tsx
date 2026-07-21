import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { assetsApi } from "../../api/assets";
import { pickFile } from "../../lib/tauri";
import { useAssetUrl } from "../../hooks/useAssetUrl";
import { useLazyVisible } from "../../hooks/useLazyVisible";
import type { AssetType } from "../../api/types";
import { ErrorBanner } from "../common/ErrorBanner";

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
  assets: { id: number; original_filename: string }[];
  onUpload: () => void;
  onImportUrl: (url: string) => void;
  onRemove: (assetId: number) => void;
}) {
  const [url, setUrl] = useState("");
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
            setUrl("");
          }
        }}
      >
        <input
          className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
          placeholder="Paste image URL, or drag a link here…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button type="submit" className="rounded border border-slate-300 px-3 py-1 text-sm">
          Import
        </button>
      </form>
      <div className="flex flex-wrap gap-3">
        {assets.map((asset) => (
          <AssetThumb
            key={asset.id}
            assetId={asset.id}
            filename={asset.original_filename}
            isImage={assetType.includes("image")}
            onRemove={() => onRemove(asset.id)}
          />
        ))}
      </div>
    </div>
  );
}

function AssetThumb({
  assetId,
  filename,
  isImage,
  onRemove,
}: {
  assetId: number;
  filename: string;
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
      <button onClick={onRemove} className="text-xs text-red-600">
        Remove
      </button>
    </div>
  );
}
