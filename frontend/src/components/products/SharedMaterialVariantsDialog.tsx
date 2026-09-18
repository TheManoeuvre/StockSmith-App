import type { SharedMaterialResolution, SharedMaterialVariantsDetail } from "../../api/types";
import { ApiError } from "../../api/client";
import { Modal } from "../common/Modal";

/**
 * Picks the shared-material 409 out of a failed generate, or null for any other error.
 *
 * The server refuses to guess whether "Primary Colour Apple / Accent Colour Apple" is a
 * variant the user wants — it lists the affected combinations and waits. Everything
 * else that can go wrong with a generate is a plain message for the ErrorBanner.
 */
export function sharedMaterialDetail(error: unknown): SharedMaterialVariantsDetail | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = error.detail as Partial<SharedMaterialVariantsDetail> | undefined;
  return detail?.code === "shared_material_variants" && Array.isArray(detail.variants)
    ? (detail as SharedMaterialVariantsDetail)
    : null;
}

/**
 * "Some combinations would use one material twice — keep them or leave them out?"
 *
 * Three-way rather than ConfirmDialog's two because neither answer is the safe default:
 * a two-tone product genuinely may or may not be sold in single-colour form. Cancel is
 * autofocused so a stray Enter creates nothing.
 */
export function SharedMaterialVariantsDialog({
  detail,
  busy,
  onResolve,
  onCancel,
}: {
  detail: SharedMaterialVariantsDetail;
  busy: boolean;
  onResolve: (resolution: Exclude<SharedMaterialResolution, "ask">) => void;
  onCancel: () => void;
}) {
  const affected = detail.variants.length;
  const rest = detail.new_variant_count - affected;
  const buttonClass = "rounded-md px-4 py-2 text-sm disabled:opacity-50 disabled:cursor-not-allowed";

  return (
    <Modal
      title="Same material on two lines"
      maxWidth="max-w-2xl"
      onClose={busy ? () => {} : onCancel}
      footer={
        <>
          <button
            autoFocus
            type="button"
            disabled={busy}
            onClick={onCancel}
            className={`${buttonClass} border border-slate-300`}
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={busy || rest === 0}
            onClick={() => onResolve("skip")}
            className={`${buttonClass} border border-slate-300`}
            title={rest === 0 ? "Every new variant is affected — there is nothing else to create" : undefined}
          >
            {busy ? "Working…" : `Leave ${affected === 1 ? "it" : "them"} out`}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onResolve("keep")}
            className={`${buttonClass} bg-slate-900 text-white`}
          >
            {busy ? "Working…" : `Keep ${affected === 1 ? "it" : "them"}`}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3 text-sm">
        <p>
          {affected === 1 ? "1 of the" : `${affected} of the`} {detail.new_variant_count} new{" "}
          {detail.new_variant_count === 1 ? "variant" : "variants"} would use the same material on more than one
          BOM line. Nothing has been created yet.
        </p>
        <ul className="flex max-h-64 flex-col gap-1 overflow-y-auto rounded border border-slate-200 bg-slate-50 p-2">
          {detail.variants.map((v) => (
            <li key={v.variant_name} className="text-xs">
              <span className="font-medium">{v.variant_name}</span>
              <span className="text-slate-600"> — {v.message.replace(`Variant '${v.variant_name}': `, "")}</span>
            </li>
          ))}
        </ul>
        <p className="text-slate-600">
          <strong className="font-medium text-slate-900">Keep</strong> creates{" "}
          {affected === 1 ? "it" : "them"} with both lines on that material, each with its own quantity — the
          variant uses the material for both parts.{" "}
          <strong className="font-medium text-slate-900">Leave out</strong> creates the other{" "}
          {rest === 1 ? "variant" : `${rest} variants`} and skips {affected === 1 ? "this one" : "these"}.
        </p>
      </div>
    </Modal>
  );
}
