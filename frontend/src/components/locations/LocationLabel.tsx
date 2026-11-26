import JsBarcode from "jsbarcode";
import { useEffect, useRef, type CSSProperties } from "react";
import { layoutFor, smallLabelShowsCode, trimmedPath, type LabelSize } from "../../lib/labels";

/** A Code 128 barcode that stretches to fill its box, so the label decides how big it is. */
function Bars({ value, style, label }: { value: string; style: CSSProperties; label: string }) {
  const ref = useRef<SVGSVGElement>(null);
  useEffect(() => {
    const svg = ref.current;
    if (!svg) return;
    JsBarcode(svg, value, { format: "CODE128", displayValue: false, margin: 0, height: 100, width: 2 });
    // JsBarcode sizes the SVG in pixels. Turn that into a viewBox so CSS can scale it to the
    // label, then let it stretch: Code 128 reads by bar proportion, not absolute size.
    const w = svg.getAttribute("width");
    const h = svg.getAttribute("height");
    if (w && h) svg.setAttribute("viewBox", `0 0 ${parseFloat(w)} ${parseFloat(h)}`);
    svg.removeAttribute("width");
    svg.removeAttribute("height");
    svg.setAttribute("preserveAspectRatio", "none");
  }, [value]);
  return <svg ref={ref} role="img" aria-label={label} style={{ display: "block", ...style }} />;
}

/**
 * One location label (docs/plan-locations.md §6, the design's "Labels" board).
 *
 * Every length is a multiple of `--mm`, so the same markup is a to-scale preview on screen
 * (`--mm` set to a few pixels) and the real thing in the print dialog (`--mm: 1mm`).
 *
 * Two layouts that differ in hierarchy, not just scale:
 * - small (51 x 25 mm): the location's own name leads, the parent path is a small line above it,
 *   and the barcode fills what's left. Nothing is ever shrunk below legible. Long names truncate
 *   with an ellipsis; long paths drop their outermost ancestors first.
 * - large (6 x 4 in): the full path becomes the headline, so it works as a shelf or bay sign
 *   from across a room, with a big barcode and the code in readable type underneath.
 */
export function LocationLabel({
  ancestors,
  name,
  code,
  size,
  mm,
}: {
  /** Names of the locations it sits inside, outermost first. Empty for a top-level location. */
  ancestors: string[];
  name: string;
  code: string;
  size: LabelSize;
  /** The length of one millimetre: "1mm" for print, e.g. "6px" for a preview. */
  mm: string;
}) {
  const u = (n: number) => `calc(${mm} * ${n})`;
  const frame: CSSProperties = {
    width: u(size.widthMm),
    height: u(size.heightMm),
    boxSizing: "border-box",
    background: "#fff",
    color: "#000",
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
    fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif",
  };
  const mono = "ui-monospace, Menlo, Consolas, monospace";
  const barcodeLabel = `Code 128 barcode for ${code}`;

  if (layoutFor(size) === "large") {
    // Scale the roomy layout with the label so a custom size that is larger or smaller than 6 x 4
    // still fills its page.
    const k = size.widthMm / 152.4;
    const hasPath = ancestors.length > 0;
    return (
      <div style={{ ...frame, padding: `${u(7 * k)} ${u(8 * k)}` }}>
        {hasPath && (
          <div style={{ fontSize: u(7 * k), lineHeight: u(8.33 * k), color: "#475569", fontWeight: 500 }}>
            {ancestors.join(" › ")} ›
          </div>
        )}
        <div
          style={{
            fontSize: u(17.33 * k),
            lineHeight: u(19.33 * k),
            fontWeight: 700,
            letterSpacing: "-0.02em",
            marginTop: u(0.33 * k),
            overflow: "hidden",
            overflowWrap: "anywhere",
          }}
        >
          {name}
        </div>
        <Bars
          value={code}
          label={barcodeLabel}
          style={{ height: u(28.33 * k), width: u(106.67 * k), margin: `auto 0 0 ${u(6.67 * k)}` }}
        />
        <div
          style={{
            fontFamily: mono,
            fontSize: u(7.33 * k),
            lineHeight: u(8.67 * k),
            width: u(106.67 * k),
            margin: `${u(1 * k)} 0 0 ${u(6.67 * k)}`,
            textAlign: "center",
            letterSpacing: "0.12em",
          }}
        >
          {code}
        </div>
      </div>
    );
  }

  // Small layout, drawn in the design at 6 px per mm and expressed here in millimetres.
  const k = Math.min(size.widthMm / 51, size.heightMm / 25);
  const pathText = ancestors.length ? trimmedPath(ancestors) : "";
  const showCode = smallLabelShowsCode(pathText);
  const twoLines = pathText.length > 44;
  const barHeight = showCode ? 7.67 : pathText === "" ? 11.67 : twoLines ? 9 : 9.33;
  return (
    <div style={{ ...frame, padding: u(2 * k) }}>
      {pathText && (
        <div
          style={{
            fontSize: u(2 * k),
            lineHeight: u(2.33 * k),
            color: "#334155",
            overflow: "hidden",
            display: "-webkit-box",
            WebkitBoxOrient: "vertical",
            WebkitLineClamp: 2,
          }}
        >
          {pathText}
        </div>
      )}
      <div
        style={{
          fontSize: u(5 * k),
          lineHeight: u(5.67 * k),
          fontWeight: 700,
          letterSpacing: "-0.01em",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {name}
      </div>
      <Bars
        value={code}
        label={barcodeLabel}
        style={{ height: u(barHeight * k), width: u((showCode ? 36.3 : 36.3) * k), margin: `${u((pathText === "" ? 1.67 : 0.83) * k)} 0 0 ${u(2.5 * k)}` }}
      />
      {showCode && (
        <div
          style={{
            fontFamily: mono,
            fontSize: u(1.83 * k),
            lineHeight: u(2.17 * k),
            width: u(36.3 * k),
            margin: `${u(0.33 * k)} 0 0 ${u(2.5 * k)}`,
            textAlign: "center",
            letterSpacing: "0.08em",
          }}
        >
          {code}
        </div>
      )}
    </div>
  );
}
