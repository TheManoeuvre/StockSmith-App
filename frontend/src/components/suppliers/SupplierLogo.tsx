import { useSupplierLogoUrl } from "../../hooks/useSupplierLogoUrl";

/**
 * A supplier's logo, or nothing. Renders no element at all when there isn't one (or it hasn't
 * loaded), so callers don't reserve an empty box — except where they want one, in which case they
 * wrap this in their own sized container.
 *
 * `alt=""`: the supplier's name is always printed beside the logo, so announcing it twice to a
 * screen reader would be noise.
 */
export function SupplierLogo({
  supplierId,
  updatedAt,
  className = "h-5 w-5",
}: {
  supplierId: number | null;
  updatedAt: string | null;
  /** Size classes for the <img>. */
  className?: string;
}) {
  const src = useSupplierLogoUrl(supplierId, updatedAt);
  if (!src) return null;
  return <img src={src} alt="" className={`${className} shrink-0 object-contain`} />;
}
