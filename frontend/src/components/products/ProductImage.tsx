import type { ReactNode } from "react";

/**
 * Product thumbnail with a parcel-icon fallback for products that have no picture.
 *
 * The icon is drawn entirely in `currentColor` and the tile background is the only other
 * colour, so theming (dark mode etc.) means changing the two classes in `PLACEHOLDER_TONE`
 * below, or swapping them for CSS variables, rather than touching the SVG.
 */
const PLACEHOLDER_TONE = "bg-slate-50 text-slate-300";

export function ParcelIcon({ className = "h-1/2 w-1/2" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      {/* box outline */}
      <path d="M21 8 12 3 3 8v8l9 5 9-5V8Z" />
      {/* top face seams */}
      <path d="m3 8 9 5 9-5" />
      <path d="M12 13v8" />
      {/* packing tape */}
      <path d="m7.5 5.5 9 5" />
    </svg>
  );
}

export function LayersIcon({ className = "h-1/2 w-1/2" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      <path d="M12 3 3 8l9 5 9-5-9-5Z" />
      <path d="m3 12 9 5 9-5" />
      <path d="m3 16 9 5 9-5" />
    </svg>
  );
}

export function ProductImage({
  src,
  alt,
  className = "",
  fallback = <ParcelIcon />,
}: {
  src: string | null | undefined;
  alt: string;
  /** Sizing/border classes for the frame, e.g. "h-16 w-16 rounded border". */
  className?: string;
  /** Shown when there is no picture; defaults to the parcel icon. */
  fallback?: ReactNode;
}) {
  return (
    <div
      className={`flex shrink-0 items-center justify-center overflow-hidden border-slate-200 ${
        src ? "bg-slate-50" : PLACEHOLDER_TONE
      } ${className}`}
    >
      {src ? (
        <img src={src} alt={alt} className="h-full w-full object-cover" />
      ) : (
        fallback
      )}
    </div>
  );
}
